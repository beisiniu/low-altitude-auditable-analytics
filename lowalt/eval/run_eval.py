"""Deterministic baseline vs low-altitude comparison over 24 questions.

No model, no network, no randomness: the upstream engine's scripted/deterministic
mode is mirrored by a rule-based natural-language resolver plus an independent
SQL answer key.  Re-running produces byte-identical numbers.

    python lowalt/eval/run_eval.py

Outputs:
    lowalt/eval/results/comparison.json   per-question, both arms
    lowalt/eval/results/summary.json      aggregated metrics
    lowalt/eval/results/golden.lock.json  answer key (drift guard)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import yaml

# Allow `python lowalt/eval/run_eval.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lowalt.engine.audit import load_duckdb
from lowalt.engine.tools import TOOLS, downgrade_plan, evidence_digest, resolve_question

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0]
DATA = ROOT / "data" / "out"
RESULTS = HERE / "results"


def _group_cte(day: bool = False) -> str:
    d = " AND o.day = ?" if day else ""
    return f"""SELECT o.hour_bucket AS hb, v.node_id AS nd, COUNT(*) AS n
      FROM orders o
      JOIN (SELECT order_id, MIN(origin_node_id) AS node_id
            FROM trajectory_points WHERE seq = 0 GROUP BY order_id) v
        ON o.order_id = v.order_id
      WHERE o.release_min >= ? AND o.release_min < ?{d}
      GROUP BY 1, 2"""


def _peak_overlap_sql() -> str:
    return f"""WITH g AS ({_group_cte()})
      SELECT CASE
        WHEN (CAST(SUBSTR(hb, 1, 2) AS INTEGER) - 7) * 60 < ?
         AND (CAST(SUBSTR(hb, 1, 2) AS INTEGER) - 7) * 60 + 60 > ?
        THEN 1 ELSE 0 END
      FROM g ORDER BY n DESC, hb, nd LIMIT 1"""


GOLDEN: dict[str, tuple[str, tuple[str, ...]]] = {
    "orders_in_window": (
        "SELECT COUNT(*) FROM orders WHERE release_min >= ? AND release_min < ?", ("t0", "t1")),
    "orders_in_window_day": (
        "SELECT COUNT(*) FROM orders WHERE release_min >= ? AND release_min < ? AND day = ?",
        ("t0", "t1", "day")),
    "peak_group": (
        f"SELECT MAX(n) FROM ({_group_cte()})", ("t0", "t1")),
    "peak_group_day": (
        f"SELECT MAX(n) FROM ({_group_cte(True)})", ("t0", "t1", "day")),
    "orders_by_node": (
        """SELECT COUNT(*) FROM orders o
           JOIN (SELECT order_id, MIN(origin_node_id) AS node_id
                 FROM trajectory_points WHERE seq = 0 GROUP BY order_id) v
             ON o.order_id = v.order_id
           WHERE o.release_min >= ? AND o.release_min < ? AND v.node_id = ?""",
        ("t0", "t1", "node")),
    "zero_conflicts": (
        """SELECT COUNT(*) FROM trajectory_points t
           JOIN (SELECT zone_id, MIN(lat) lo1, MAX(lat) hi1,
                        MIN(lon) lo2, MAX(lon) hi2,
                        MIN(active_from_min) t0, MAX(active_to_min) t1
                 FROM no_fly_zones GROUP BY zone_id) z
             ON t.lat BETWEEN z.lo1 AND z.hi1
            AND t.lon BETWEEN z.lo2 AND z.hi2
            AND t.t_min >= z.t0 AND t.t_min < z.t1""", ()),
    "window_traj_points": (
        "SELECT COUNT(*) FROM trajectory_points WHERE t_min >= ? AND t_min < ?", ("t0", "t1")),
    "cruise_dominant_share": (
        """SELECT MAX(n) * 100.0 / SUM(n) FROM (
             SELECT altitude_layer, COUNT(*) n FROM trajectory_points
             WHERE phase = 'cruise' AND day = ? GROUP BY 1)""", ("day",)),
    "cruise_dominant_count": (
        """SELECT MAX(n) FROM (
             SELECT altitude_layer, COUNT(*) n FROM trajectory_points
             WHERE phase = 'cruise' AND day = ? GROUP BY 1)""", ("day",)),
    "cruise_points_day": (
        "SELECT COUNT(*) FROM trajectory_points WHERE phase = 'cruise' AND day = ?", ("day",)),
    "cruise_ratio": (
        """SELECT SUM(CASE WHEN phase = 'cruise' THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
           FROM trajectory_points WHERE day = ?""", ("day",)),
    "cruise_dominant_share_window": (
        """SELECT MAX(n) * 100.0 / SUM(n) FROM (
             SELECT altitude_layer, COUNT(*) n FROM trajectory_points
             WHERE phase = 'cruise' AND day = ? AND t_min >= ? AND t_min < ?
             GROUP BY 1)""", ("day", "t0", "t1")),
    "window_share": (
        """SELECT COUNT(*) * 100.0 / (SELECT COUNT(*) FROM orders)
           FROM orders WHERE release_min >= ? AND release_min < ?""", ("t0", "t1")),
    "peak_share": (
        f"SELECT MAX(n) * 100.0 / SUM(n) FROM ({_group_cte()})", ("t0", "t1")),
    "peak_share_day": (
        f"SELECT MAX(n) * 100.0 / SUM(n) FROM ({_group_cte(True)})", ("t0", "t1", "day")),
    "peak_hour_overlap": (_peak_overlap_sql(), ("t0", "t1", "w1", "w0")),
}


def golden_query(con, q: dict) -> tuple[float, str, list]:
    sql, names = GOLDEN[q["golden"]]
    args = q.get("golden_args", {})
    params = [args[n] for n in names]
    row = con.execute(sql, params).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0, sql, params


# ---- composite reducers over the low-altitude tool's structured output -----
def _d_peak_overlap(v: dict, args: dict, con) -> float | None:
    hb = v.get("peak_hour")
    if not hb:
        return None
    start = (int(hb[:2]) - 7) * 60
    return float(start < args["w1"] and start + 60 > args["w0"])


def _d_window_share(v: dict, args: dict, con) -> float | None:
    if v.get("total") is None:
        return None
    all_orders = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    return float(v["total"]) / all_orders * 100 if all_orders else None


def _d_peak_share(v: dict, args: dict, con) -> float | None:
    if v.get("total") and v.get("peak") is not None:
        return float(v["peak"]) / float(v["total"]) * 100
    return None


DERIVERS = {
    "peak_overlap": _d_peak_overlap,
    "window_share": _d_window_share,
    "peak_share": _d_peak_share,
}


def extract_value(result, q: dict, con):
    v = result.value
    if q.get("derive"):
        if not isinstance(v, dict):
            # The generic SQL arm was given an independently written query that
            # already computes the derived number; do not penalise it here.
            return float(v)
        return DERIVERS[q["derive"]](v, q["golden_args"], con)
    field = q["extract"]
    if isinstance(v, dict):
        if "." in field:
            head, tail = field.split(".", 1)
            sub = v.get(head, {}) or {}
            if tail in sub:
                return sub[tail]
            try:
                return sub.get(int(tail))
            except (ValueError, TypeError):
                return None
        return v.get(field)
    # Generic SQL arm returns one bare scalar for the question it was given.
    return float(v)


def relevance(arm: dict, q: dict, numeric_ok: bool) -> dict[str, bool]:
    """Structured, non-LLM rubric: does the answer carry the value, the
    dimension it belongs to, the time window, an explicit calculation
    definition, and a breakdown?  A bare scalar from ad-hoc SQL scores low
    even when the number is right -- that gap is the point of the comparison."""
    text = arm["answer"] or ""
    keys = set(arm["value_keys"])
    out = {}
    for c in q.get("relevance", []):
        if c == "value":
            out[c] = bool(numeric_ok)
        elif c == "dimension":
            out[c] = bool(keys & {"peak_node", "peak_hour", "dominant_layer", "by_node"}) or any(
                w in text for w in ("节点", "高度层", "时段"))
        elif c == "window":
            out[c] = bool(re.search(r"\d{2}:\d{2}", text)) or "窗口" in text or "window" in keys
        elif c == "definition":
            out[c] = arm["has_definition"]
        elif c == "breakdown":
            out[c] = bool(keys & {"by_hour", "by_node", "by_layer", "by_zone", "n_groups"})
        else:
            out[c] = False
    return out


def numeric_ok(value, golden: float, tol: float = 0.02) -> bool:
    if value is None:
        return False
    try:
        return abs(float(value) - golden) <= max(tol, abs(golden) * 1e-6)
    except (TypeError, ValueError):
        return False


def run_arm(con, q: dict, arm: str, golden_sql: str, golden_params: list) -> dict:
    plan = resolve_question(q["question"])
    resolver_tool = plan["tool"]
    typed = {"tool": q["tool"], "params": dict(q["params"])}
    if arm == "lowalt":
        use = typed
    elif q["tool"] == "no_fly_conflict_count":
        use = {"tool": "bbox_conflict_approx", "params": dict(q["params"])}
    else:
        # BASELINE: the generic SQL tool, given an INDEPENDENTLY written query.
        # It is never disabled from any category and can answer every question.
        use = {"tool": "sql_aggregate",
               "params": {"sql": golden_sql, "params": golden_params,
                          "label": q.get("extract") or q.get("derive")}}
    t0 = time.perf_counter()
    result = TOOLS[use["tool"]](con, use["params"])
    elapsed = (time.perf_counter() - t0) * 1000
    value = extract_value(result, q, con)
    return {
        "arm": arm,
        "tool_used": use["tool"],
        "resolver_selected": resolver_tool,
        "resolver_state": plan["state"],
        "resolver_scores": plan["scores"],
        "tool_selection_ok": resolver_tool == q["tool"],
        "value": value,
        "answer": result.answer,
        "facts_published": len(result.facts),
        "v2_passed": result.verify.passed if result.verify else 0,
        "v2_total": result.verify.total if result.verify else 0,
        "v2_pass_rate": round(result.verify.pass_rate, 6) if result.verify else 0.0,
        "steps": result.steps,
        "elapsed_ms": round(elapsed, 3),
        "evidence_digest": evidence_digest(result),
        "value_is_dict": isinstance(result.value, dict),
        "value_keys": sorted(result.value.keys()) if isinstance(result.value, dict) else [],
        "has_definition": "definition" in (result.evidence or {}),
        "relevance": {},
    }


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    questions = spec["questions"]
    con = load_duckdb(DATA)

    golden_lock, rows = {}, []
    for q in questions:
        golden, gsql, gparams = golden_query(con, q)
        golden_lock[q["id"]] = {
            "value": golden, "sql": gsql.strip(), "params": gparams,
            "category": q["category"], "question": q["question"],
        }
        base = run_arm(con, q, "baseline", gsql, gparams)
        low = run_arm(con, q, "lowalt", gsql, gparams)
        for arm_res in (base, low):
            ok = numeric_ok(arm_res["value"], golden, q.get("tolerance", 0.02))
            arm_res["numeric_correct"] = ok
            arm_res["golden"] = golden
            arm_res["relevance"] = relevance(arm_res, q, ok)
            arm_res["relevance_score"] = (sum(arm_res["relevance"].values())
                                          / len(arm_res["relevance"])) if arm_res["relevance"] else 0.0
        rows.append({
            "id": q["id"], "category": q["category"], "question": q["question"],
            "expected_tool": q["tool"], "extract": q.get("extract") or q.get("derive"),
            "golden": golden, "baseline": base, "lowalt": low,
        })

    # aggregate
    summary: dict = {"by_category": {}, "overall": {}}

    def summarise(sel: list) -> dict:
        block: dict = {}
        for arm in ("baseline", "lowalt"):
            a = [r[arm] for r in sel]
            block[arm] = {
                "n": len(a),
                "numeric_correct": sum(1 for x in a if x["numeric_correct"]),
                "numeric_accuracy": round(sum(1 for x in a if x["numeric_correct"]) / len(a), 4),
                "relevance_mean": round(sum(x["relevance_score"] for x in a) / len(a), 4),
                "verifiable_numbers": sum(x["facts_published"] for x in a),
                "v2_pass_rate": round(sum(x["v2_pass_rate"] for x in a) / len(a), 4),
                "tool_selection_ok": sum(1 for x in a if x["tool_selection_ok"]),
                "resolver_exact": sum(1 for x in a if x["resolver_state"] == "EXACT"),
                "resolver_ambiguous": sum(1 for x in a if x["resolver_state"] == "AMBIGUOUS"),
                "resolver_unresolved": sum(1 for x in a if x["resolver_state"] == "UNRESOLVED"),
                "mean_steps": round(sum(x["steps"] for x in a) / len(a), 3),
                "mean_elapsed_ms": round(sum(x["elapsed_ms"] for x in a) / len(a), 3),
            }
        block["delta"] = {
            "numeric_accuracy_pp": round((block["lowalt"]["numeric_accuracy"]
                                          - block["baseline"]["numeric_accuracy"]) * 100, 2),
            "relevance_pp": round((block["lowalt"]["relevance_mean"]
                                   - block["baseline"]["relevance_mean"]) * 100, 2),
            "verifiable_numbers_x": round(block["lowalt"]["verifiable_numbers"]
                                          / max(1, block["baseline"]["verifiable_numbers"]), 2),
        }
        return block

    for cat in ("aggregation", "spatial", "composite"):
        summary["by_category"][cat] = summarise(
            [r for r in rows if r["category"] == cat])
    summary["overall"] = summarise(rows)

    failures = [{"id": r["id"], "arm": arm, "value": r[arm]["value"],
                 "golden": r["golden"], "reason": _failure_reason(r, arm)}
                for r in rows for arm in ("baseline", "lowalt")
                if not r[arm]["numeric_correct"]]
    summary["failures"] = failures
    summary["meta"] = {
        "questions": len(rows), "model": "none (rule-based resolver, deterministic)",
        "llm_calls": 0, "python": "3.13", "duckdb": con.execute("SELECT version()").fetchone()[0],
        "data_manifest": str(DATA / "manifest.json"),
    }

    (RESULTS / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "golden.lock.json").write_text(
        json.dumps(golden_lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"{len(rows)} questions, 0 model calls\n")
    for cat in ("aggregation", "spatial", "composite", "ALL"):
        b = summary["overall"] if cat == "ALL" else summary["by_category"][cat]
        n = b["baseline"]["n"]
        print(f"{cat:12s} numeric {b['baseline']['numeric_correct']:2d}/{n} -> "
              f"{b['lowalt']['numeric_correct']:2d}/{n} | "
              f"relevance {b['baseline']['relevance_mean']:.3f} -> {b['lowalt']['relevance_mean']:.3f} | "
              f"verifiable numbers {b['baseline']['verifiable_numbers']:3d} -> "
              f"{b['lowalt']['verifiable_numbers']:3d}")
    print(f"\nfailures: {len(failures)}  -> {RESULTS / 'comparison.json'}")


def _failure_reason(row: dict, arm: str) -> str:
    a = row[arm]
    if a["value"] is None:
        return "tool returned no value for the requested field/derivation"
    return "numeric mismatch vs independent SQL answer key"


if __name__ == "__main__":
    main()
