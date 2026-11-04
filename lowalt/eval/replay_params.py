"""Offline replay of the `llm_tools` arm after fixing the orchestration layer.

WHY: the first 7B run used a prompt saying "never invent a time window", which
the better-behaved 7B model followed so literally that it returned `params: {}`
and the tools silently fell back to the full-day default.  That is an
orchestration defect, not a model-capability result.

Because the experiment is deterministic (temperature=0, seed=42), the saved
RAW MODEL OUTPUTS are sufficient: we re-parse them, apply the FIXED
`normalize_tool_params` (which now both prunes and fills), re-execute the tools,
and re-score against the same independent SQL answer key.  No re-inference, no
changed model outputs.

    python lowalt/eval/replay_params.py
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from lowalt.engine.audit import load_duckdb  # noqa: E402
from lowalt.engine.tools import TOOLS  # noqa: E402
from lowalt.eval.run_eval import extract_value, numeric_ok, relevance  # noqa: E402
from lowalt.eval.run_llm_eval import clean_fence, normalize_tool_params  # noqa: E402

RESULTS = HERE / "results"
QUESTIONS = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))["questions"]
QMAP = {q["id"]: q for q in QUESTIONS}
ARMS = ("llm_sql", "llm_sql_focused", "llm_tools")


def summarise(rows: list, arm: str) -> dict:
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


def replay_one(con, tag: str) -> tuple[int, int]:
    comp_path = RESULTS / f"llm_comparison_{tag}.json"
    summ_path = RESULTS / f"llm_summary_{tag}.json"
    if not comp_path.exists():
        print(f"skip {tag}: no {comp_path.name}")
        return 0, 0
    rows = json.loads(comp_path.read_text(encoding="utf-8"))
    shutil.copy(comp_path, RESULTS / f"llm_comparison_{tag}_prereplay.json")
    shutil.copy(summ_path, RESULTS / f"llm_summary_{tag}_prereplay.json")
    before = sum(1 for r in rows for rp in r["repeats"] if rp["llm_tools"].get("numeric_correct"))

    changed = 0
    for row in rows:
        q = QMAP[row["id"]]
        for rep in row["repeats"]:
            a = rep["llm_tools"]
            raw = a.get("raw", "")
            text = clean_fence(raw)
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                continue
            try:
                plan = json.loads(m.group(0))
                name, params = plan["tool"], dict(plan.get("params") or {})
            except Exception:  # noqa: BLE001
                continue
            if name not in ("drone_demand_aggregate", "no_fly_conflict_count",
                            "altitude_distribution"):
                continue
            params = normalize_tool_params(name, params, q["question"])
            try:
                result = TOOLS[name](con, params)
            except Exception as exc:  # noqa: BLE001
                a["error"] = f"replay tool failed: {exc}"
                continue
            arm = {"answer": result.answer,
                   "value_keys": sorted(result.value.keys()) if isinstance(result.value, dict) else [],
                   "has_definition": "definition" in (result.evidence or {})}
            value = extract_value(result, q, con)
            ok = numeric_ok(value, row["golden"], q.get("tolerance", 0.02))
            a.update({
                "parsed": True, "value": value, "numeric_correct": ok,
                "params": params, "tool": name,
                "facts_published": len(result.facts),
                "v2_pass_rate": round(result.verify.pass_rate, 4) if result.verify else 0.0,
                "relevance": relevance(arm, q, ok),
            })
            a["relevance_score"] = (sum(a["relevance"].values()) / len(a["relevance"])
                                    if a["relevance"] else 0.0)
            a["replayed"] = True
            changed += 1

    after = sum(1 for r in rows for rp in r["repeats"] if rp["llm_tools"].get("numeric_correct"))
    comp_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    summary = json.loads(summ_path.read_text(encoding="utf-8"))
    summary.update({a: summarise(rows, a) for a in ARMS})
    summary["by_category_accuracy"] = {
        cat: {a: round(sum(1 for s in (rp for r in rows if r["category"] == cat
                                       for rp in r["repeats"])
                           if s[a].get("numeric_correct")) / max(1, len(
                               [rp for r in rows if r["category"] == cat for rp in r["repeats"]])), 4)
              for a in ARMS}
        for cat in ("aggregation", "spatial", "composite")
    }
    summary["meta"]["orchestration"] = (
        "replayed with normalize_tool_params v2 (prune hallucinated params AND "
        "fill question-derived day/time-window/zone); raw model outputs unchanged")
    summ_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(f"{tag}: llm_tools {before}/24 -> {after}/24  ({changed} rows replayed)")
    return before, after


def main() -> None:
    con = load_duckdb(REPO / "lowalt" / "data" / "out")
    for tag in ("1p5b", "3b", "7b"):
        replay_one(con, tag)


if __name__ == "__main__":
    main()
