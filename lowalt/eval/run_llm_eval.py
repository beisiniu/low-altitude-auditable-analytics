"""Supplementary experiment: does a tool layer help when a REAL model is in the loop?

Four measured arms over the same 24 questions and the same independent SQL
answer key (plus the deterministic rule_tools arm from comparison.json):

  llm_sql          model given schema + ALL definitions, writes its own SQL
  llm_sql_focused  FAVOURABLE ablation: only the definitions relevant to that
                   question, to test whether llm_sql's failures are a
                   long-prompt artefact rather than an SQL-generation failure
  llm_tools_typed  (snapshot in llm_*_typed_only.json) model chooses ONE tool,
                   params get type/range validation only
  llm_tools        model chooses ONE tool; params get type validation AND
                   deterministic question-constraint validation; the tool
                   computes, publishes Facts and runs the V2 verifier

FAIRNESS NOTE: llm_sql and llm_tools receive the SAME schema and the SAME
encoded definitions.  llm_sql_focused is deliberately an advantage for the SQL
arm.  The constraint validation reads ONLY the question text -- never the
golden answer or the expected tool -- and is itself part of what a tool layer
provides.  See EVAL_REPORT.md §7 for the limitations, including that these
numbers are specific to a local 1.5B CPU model and must not be generalised.

Local only: llama.cpp server on 127.0.0.1, no API key, no data leaves the box.

    python lowalt/eval/run_llm_eval.py --start-server --limit 3   # smoke
    python lowalt/eval/run_llm_eval.py --start-server              # full 24
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from lowalt.engine.audit import load_duckdb  # noqa: E402
from lowalt.engine.tools import TOOLS  # noqa: E402
from lowalt.eval.run_eval import (  # noqa: E402
    extract_value,
    golden_query,
    numeric_ok,
    relevance,
)

DEFAULT_URL = os.environ.get("LOWALT_LLM_URL", "http://127.0.0.1:8081")
DEFAULT_MODEL_NAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"


def _asset_roots() -> list[Path]:
    """Portable lookup: a receiving computer need not have an E: drive.

    Priority: LOWALT_MODEL_ROOT > <repo>/models > <handoff>/models >
    the original E:/lowalt-llm.  Both flat (root/*.gguf) and nested
    (root/models/*.gguf) layouts are accepted.
    """
    repo = Path(__file__).resolve().parents[2]
    roots: list[Path] = []
    if os.environ.get("LOWALT_MODEL_ROOT"):
        roots.append(Path(os.environ["LOWALT_MODEL_ROOT"]))
    roots += [repo / "models", repo.parent / "models", Path("E:/lowalt-llm")]
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _locate(filename: str, in_bin: bool = False) -> Path:
    for root in _asset_roots():
        candidates = ([root / "bin" / filename, root / filename] if in_bin
                      else [root / filename, root / "models" / filename])
        for p in candidates:
            if p.exists():
                return p
    return _asset_roots()[0] / ("bin" if in_bin else "models") / filename


DEFAULT_EXE = Path(os.environ.get(
    "LOWALT_LLM_EXE", str(_locate("llama-server.exe", in_bin=True))))
DEFAULT_MODEL = Path(os.environ.get(
    "LOWALT_LLM_MODEL", str(_locate(DEFAULT_MODEL_NAME))))
RESULTS = HERE / "results"

SCHEMA = """DuckDB tables (read-only):
  orders(order_id, day INTEGER 0..4, release_min DOUBLE, hour_bucket,
         customer_node_id, weight_kg, service_min, earliest_min, latest_min)
  trajectory_points(traj_id, order_id, day, seq, t_min DOUBLE, lat, lon,
         altitude_m, altitude_layer, phase, origin_node_id)
  no_fly_zones(zone_id, vertex_seq, lat, lon, active_from_min, active_to_min)
"""

DEFINITION_BLOCKS = {
    "time": """- TIME: release_min / t_min = minutes since 07:00.
  07:00=0, 14:00=420, 15:00=480, 16:00=540, 17:00=600, 18:00=660, 19:00=720.
  "14:00—16:00" is a WINDOW on release_min, NOT a day filter.
  "全部 5 天" / "all 5 days" means NO day filter. day can only be 0,1,2,3,4.""",
    "volume": """- "订单总量 / 配送量 / how many orders" = COUNT(*) of orders.
  NEVER SUM(weight_kg). Half-open window: release_min>=a AND release_min<b.""",
    "peak": """- Launch vertiport = trajectory_points.origin_node_id WHERE seq=0.
- Demand PEAK = MAX over groups (hour_bucket, origin_node_id) after joining
  orders to trajectory_points ON order_id with seq=0.
- Share / percent = part*100.0/whole.""",
    "altitude": """- Altitude stats: ONLY phase='cruise' rows, GROUP BY altitude_layer.
  Dominant-layer share = MAX(n)*100.0/SUM(n).""",
    "conflict": """- Conflict events, hit points and conflict minutes are 0 on THIS dataset
  (the no-fly zones do not intersect the trajectory network).
- Trajectory points in a zone's ACTIVE WINDOW = COUNT(*) FROM trajectory_points
  WHERE t_min>=start AND t_min<end; airport 480..660, temporary 600..720.""",
}

# Which definition blocks a question actually needs. Used ONLY by the
# "focused prompt" ablation arm, which gives the SQL arm a FAVOURABLE
# condition (less distracting text) to check whether 0/24 is a long-prompt
# artefact rather than a genuine SQL-generation failure.
_FOCUS_BY_GOLDEN = {
    "orders_in_window": ("time", "volume"),
    "orders_in_window_day": ("time", "volume"),
    "orders_by_node": ("time", "volume"),
    "peak_group": ("time", "volume", "peak"),
    "peak_group_day": ("time", "volume", "peak"),
    "peak_share": ("time", "volume", "peak"),
    "peak_share_day": ("time", "volume", "peak"),
    "peak_hour_overlap": ("time", "volume", "peak"),
    "window_share": ("time", "volume"),
    "zero_conflicts": ("time", "conflict"),
    "window_traj_points": ("time", "conflict"),
    "cruise_dominant_share": ("time", "altitude"),
    "cruise_dominant_count": ("time", "altitude"),
    "cruise_points_day": ("time", "altitude"),
    "cruise_ratio": ("time", "altitude"),
    "cruise_dominant_share_window": ("time", "altitude"),
}

ALL_DEFINITIONS = "\n".join(DEFINITION_BLOCKS.values())


def sql_system_for(q: dict, focused: bool) -> str:
    blocks = (_FOCUS_BY_GOLDEN.get(q["golden"], ("time", "volume"))
              if focused else tuple(DEFINITION_BLOCKS))
    return f"""You are a data analyst writing DuckDB SQL.
{SCHEMA}
Rules:
{chr(10).join(DEFINITION_BLOCKS[b] for b in blocks)}
Return ONLY one read-only SELECT/WITH query that produces the single number
asked for. No explanation, no markdown fences."""


TOOL_SYSTEM = f"""You are a router for a low-altitude logistics analytics toolkit.
{SCHEMA}
Rules:
{ALL_DEFINITIONS}
Choose the ONE tool that answers the question and fill its parameters.
ALWAYS fill from_hhmm / to_hhmm when the question names a time range
(e.g. "14:00—16:00"). Omit an optional parameter ONLY when the question
truly does not mention it. Never invent a day, zone or trajectory id.
Tools:
  1) drone_demand_aggregate  params {{from_hhmm, to_hhmm, day? 0..4}}
       order volumes, peaks, by-hour/by-node breakdowns, shares in a window
  2) no_fly_conflict_count   params {{day? 0..4,
       zone_id? one of z_airport_szx|z_temp_baoan_event, traj_id?}}
       conflict events / hit points / conflict minutes / trajectory-point
       counts inside a zone's active window
  3) altitude_distribution   params {{day? 0..4, from_hhmm?, to_hhmm?}}
       altitude layers, cruise-point counts, dominant layer share
Return ONLY a JSON object like {{"tool":"...","params":{{...}}}}.
No explanation, no markdown fences."""

_UNSAFE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|COPY|PRAGMA|INSTALL|LOAD|"
    r"EXPORT|IMPORT|VACUUM|SET|CALL)\b", re.I)
_TIME_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[—\-~～至到]\s*(\d{1,2}):(\d{2})")
_DAY_RE = re.compile(r"第\s*([0-4])\s*天|day\s*([0-4])", re.I)
_TEMP_ZONE_RE = re.compile(r"体育馆|临时|stadium|z_temp", re.I)
_ZONE_RE = re.compile(r"机场|airport|体育馆|临时|stadium|z_airport|z_temp", re.I)


def normalize_tool_params(name: str, params: dict, question: str) -> dict:
    """Local orchestration layer: typed validation + question-derived
    constraints.  Reads ONLY the question text -- never the golden answer or
    the expected tool.

    It must both PRUNE hallucinated values (e.g. a legal-but-wrong day=4) and
    FILL values the model omitted (e.g. a 7B model that takes "never invent a
    time window" too literally and returns `{}`).  Prune-only orchestration is
    incomplete: a better-behaved model can score worse than a sloppier one.
    """
    p = dict(params)
    # --- typed validation ---
    if "day" in p:
        try:
            d = int(p["day"])
            if 0 <= d <= 4:
                p["day"] = d
            else:
                p.pop("day")
        except (ValueError, TypeError):
            p.pop("day")
    for key in ("from_hhmm", "to_hhmm"):
        v = p.get(key)
        if v is None:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(v))
        if not m or not (7 <= int(m.group(1)) <= 19):
            p.pop(key)
    if p.get("zone_id") not in (None, "z_airport_szx", "z_temp_baoan_event"):
        p.pop("zone_id", None)
    if p.get("traj_id") is not None and not re.fullmatch(r"T-[A-Za-z0-9\-]+", str(p["traj_id"])):
        p.pop("traj_id")

    # --- question-derived constraints: prune AND fill ---
    dm = _DAY_RE.search(question)
    if dm:
        p["day"] = int(dm.group(1) or dm.group(2))
    else:
        p.pop("day", None)
    wm = _TIME_RANGE_RE.search(question)
    if wm and name in ("drone_demand_aggregate", "altitude_distribution"):
        p["from_hhmm"] = f"{int(wm.group(1)):02d}:{wm.group(2)}"
        p["to_hhmm"] = f"{int(wm.group(3)):02d}:{wm.group(4)}"
    elif not wm:
        p.pop("from_hhmm", None)
        p.pop("to_hhmm", None)
    zm = _ZONE_RE.search(question)
    if zm:
        p["zone_id"] = ("z_temp_baoan_event" if _TEMP_ZONE_RE.search(question)
                        else "z_airport_szx")
    else:
        p.pop("zone_id", None)
    return p


def start_server(exe: Path, model: Path, port: int, ctx: int = 1024,
                 threads: int = 4) -> subprocess.Popen:
    if not exe.exists():
        raise FileNotFoundError(f"llama-server not found: {exe}")
    if not model.exists():
        raise FileNotFoundError(f"model not found: {model}")
    # --no-repack: newer llama.cpp allocates an extra ~600 MB contiguous
    # CPU_REPACK buffer by default; on a memory-constrained host that alone
    # prevents the model from loading.  -c 1024 fits the schema+definitions
    # prompt and a 160-token answer without wasting KV cache.
    cmd = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
           "-c", str(ctx), "-t", str(threads), "--no-repack"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(180):
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited before becoming healthy")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1)
    proc.terminate()
    raise RuntimeError("llama-server did not become healthy within 180 s")


def chat(url: str, system: str, question: str, max_tokens: int, timeout: int):
    body = {
        "model": "local-qwen2.5-1.5b",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": question}],
        "temperature": 0, "seed": 42, "top_p": 1.0,
        "max_tokens": max_tokens, "stream": False,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    elapsed = (time.perf_counter() - t0) * 1000
    return (data["choices"][0]["message"]["content"],
            data.get("usage", {}), round(elapsed, 2))


def clean_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql|json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def run_llm_sql(con, url: str, q: dict, golden: float, max_tokens: int,
                timeout: int, focused: bool = False) -> dict:
    out: dict = {"parsed": False, "value": None, "numeric_correct": False,
                 "prompt": "focused" if focused else "full"}
    try:
        raw, usage, ms = chat(url, sql_system_for(q, focused), q["question"],
                              max_tokens, timeout)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"request failed: {exc}"
        return out
    out.update({"raw": raw[:800], "usage": usage, "elapsed_ms": ms})
    sql = clean_fence(raw)
    out["sql"] = sql[:800]
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I) or _UNSAFE.search(sql):
        out["error"] = "rejected: not a read-only SELECT/WITH"
        return out
    try:
        row = con.execute(sql).fetchone()
        out["parsed"] = True
        out["value"] = float(row[0]) if row and row[0] is not None else 0.0
        out["numeric_correct"] = numeric_ok(out["value"], golden, q.get("tolerance", 0.02))
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"SQL failed: {type(exc).__name__}: {str(exc)[:200]}"
    return out


def run_llm_tools(con, url: str, q: dict, golden: float, max_tokens: int, timeout: int) -> dict:
    out: dict = {"parsed": False, "value": None, "numeric_correct": False}
    try:
        raw, usage, ms = chat(url, TOOL_SYSTEM, q["question"], max_tokens, timeout)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"request failed: {exc}"
        return out
    out.update({"raw": raw[:800], "usage": usage, "elapsed_ms": ms})
    text = clean_fence(raw)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        out["error"] = "no JSON object in output"
        return out
    try:
        plan = json.loads(match.group(0))
        name, params = plan["tool"], dict(plan.get("params") or {})
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"bad JSON: {exc}"
        return out
    out["tool"] = name
    out["params"] = params
    out["tool_selection_ok"] = name == q["tool"]
    if name not in ("drone_demand_aggregate", "no_fly_conflict_count", "altitude_distribution"):
        out["error"] = f"unknown tool {name}"
        return out
    # Local orchestration layer: typed validation + question-derived
    # constraints (prune hallucinated values AND fill omitted ones).
    params = normalize_tool_params(name, params, q["question"])
    out["params"] = params

    try:
        result = TOOLS[name](con, params)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"tool failed: {type(exc).__name__}: {str(exc)[:200]}"
        return out
    arm = {"answer": result.answer,
           "value_keys": sorted(result.value.keys()) if isinstance(result.value, dict) else [],
           "has_definition": "definition" in (result.evidence or {})}
    value = extract_value(result, q, con)
    ok = numeric_ok(value, golden, q.get("tolerance", 0.02))
    out.update({
        "parsed": True, "value": value, "numeric_correct": ok,
        "facts_published": len(result.facts),
        "v2_pass_rate": round(result.verify.pass_rate, 4) if result.verify else 0.0,
        "relevance": relevance(arm, q, ok),
    })
    out["relevance_score"] = (sum(out["relevance"].values()) / len(out["relevance"])
                              if out["relevance"] else 0.0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-server", action="store_true")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--ctx", type=int, default=1024)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="0 = all 24")
    ap.add_argument("--tag", default="", help="suffix for result files, e.g. 7b / 3b")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=320)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    questions = spec["questions"][: args.limit] if args.limit else spec["questions"]
    con = load_duckdb(REPO / "lowalt" / "data" / "out")

    proc = None
    if args.start_server:
        print("starting local llama-server (CPU, 127.0.0.1 only) ...", flush=True)
        proc = start_server(Path(args.exe), Path(args.model), args.port,
                            ctx=args.ctx, threads=args.threads)
        print("server healthy", flush=True)

    rows, errors = [], []
    try:
        for i, q in enumerate(questions, start=1):
            golden, _sql, _params = golden_query(con, q)
            rec = {"id": q["id"], "category": q["category"],
                   "question": q["question"], "expected_tool": q["tool"],
                   "golden": golden, "repeats": []}
            for r in range(args.repeat):
                s = run_llm_sql(con, args.url, q, golden, args.max_tokens, args.timeout)
                f = run_llm_sql(con, args.url, q, golden, args.max_tokens,
                                args.timeout, focused=True)
                t = run_llm_tools(con, args.url, q, golden, args.max_tokens, args.timeout)
                rep = {"repeat": r, "llm_sql": s, "llm_sql_focused": f, "llm_tools": t}
                rec["repeats"].append(rep)
                for arm, res in rep.items():
                    if arm == "repeat":
                        continue
                    if res.get("error"):
                        errors.append({"id": q["id"], "arm": arm, "reason": res["error"]})
            last = rec["repeats"][-1]
            mark = lambda a: "OK" if last[a]["numeric_correct"] else "X"  # noqa: E731
            print(f"[{i:2d}/{len(questions)}] {q['id']} "
                  f"sql={mark('llm_sql'):2s} focused={mark('llm_sql_focused'):2s} "
                  f"tools={mark('llm_tools'):2s}", flush=True)
            rows.append(rec)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def summarise(arm: str) -> dict:
        runs = [rep[arm] for r in rows for rep in r["repeats"]]
        return {
            "n": len(runs),
            "parsed": sum(1 for x in runs if x.get("parsed")),
            "numeric_correct": sum(1 for x in runs if x.get("numeric_correct")),
            "numeric_accuracy": round(sum(1 for x in runs if x.get("numeric_correct")) / len(runs), 4),
            "relevance_mean": round(sum(x.get("relevance_score", 0.0) for x in runs) / len(runs), 4),
            "tool_selection_ok": sum(1 for x in runs if x.get("tool_selection_ok")),
            "verifiable_numbers": sum(x.get("facts_published", 0) for x in runs),
            "mean_elapsed_ms": round(sum(x.get("elapsed_ms", 0) for x in runs) / len(runs), 2),
        }

    arms = ("llm_sql", "llm_sql_focused", "llm_tools")
    summary = {
        **{a: summarise(a) for a in arms},
        "errors": errors,
        "meta": {
            "questions": len(rows), "repeats": args.repeat,
            "model": Path(args.model).name, "engine": "llama.cpp CPU",
            "endpoint": args.url, "temperature": 0, "seed": 42,
            "api_key_required": False, "data_left_host": False,
            "fairness": ("llm_sql and llm_tools receive identical schema + all definitions; "
                         "llm_sql_focused is a favourable ABLATION (only the definitions "
                         "relevant to that question), used to test whether the SQL arm's "
                         "failure is a long-prompt artefact"),
            "tool_arm_validation": ("typed params are validated against schema: day 0..4, "
                                    "HH:MM within 07:00-19:00, zone_id whitelist, traj_id pattern"),
        },
    }
    by_cat = {}
    for cat in ("aggregation", "spatial", "composite"):
        subset = [rep for r in rows if r["category"] == cat for rep in r["repeats"]]
        if subset:
            by_cat[cat] = {
                a: round(sum(1 for x in (s[a] for s in subset)
                             if x.get("numeric_correct")) / len(subset), 4)
                for a in arms
            }
    summary["by_category_accuracy"] = by_cat

    suffix = f"_{args.tag}" if args.tag else ""
    comparison_path = RESULTS / f"llm_comparison{suffix}.json"
    summary_path = RESULTS / f"llm_summary{suffix}.json"
    comparison_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n=== summary ===")
    for arm in arms:
        s = summary[arm]
        print(f"{arm:10s} parsed {s['parsed']}/{s['n']}  numeric {s['numeric_correct']}/{s['n']} "
              f"({s['numeric_accuracy']:.3f})  relevance {s['relevance_mean']:.3f}  "
              f"tool_select {s['tool_selection_ok']}  mean {s['mean_elapsed_ms']:.0f} ms")
    print(f"errors: {len(errors)}  -> {comparison_path}")


if __name__ == "__main__":
    main()
