"""Summarise the model-scaling ablation across local model sizes.

Reads whichever `results/llm_summary_<tag>.json` files exist and writes a
Markdown table to `results/model_scaling.md`.  Pure file IO; no model needed.

    python lowalt/eval/compare_models.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

MODELS = [
    ("0.5B (fallback, not run)", "05b"),
    ("1.5B Q4_K_M", "1p5b"),
    ("3B Q4_K_M", "3b"),
    ("7B Q3_K_M", "7b"),
]


def load(tag: str) -> dict | None:
    p = RESULTS / f"llm_summary_{tag}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    lines = [
        "# 模型规模消融：工具层收益是否随模型变强而消失",
        "",
        "同一 24 题、同一独立 SQL 答案键、同一 prompt、`temperature=0 / seed=42`，"
        "全部本地 llama.cpp CPU 推理，无 API key。",
        "",
        "| 模型 | LLM 自写 SQL | 聚焦 prompt SQL | LLM 选工具 | 工具选择正确 | 答案完整性 | 平均耗时 |",
        "|---|---|---|---|---|---|---|",
    ]
    found = 0
    for label, tag in MODELS:
        s = load(tag)
        if s is None:
            continue
        found += 1
        a = s["llm_sql"]["numeric_correct"]
        f = s["llm_sql_focused"]["numeric_correct"]
        t = s["llm_tools"]["numeric_correct"]
        sel = s["llm_tools"]["tool_selection_ok"]
        rel = s["llm_tools"]["relevance_mean"]
        ms = s["llm_tools"]["mean_elapsed_ms"]
        lines.append(
            f"| {label} | {a}/24 ({a/24:.1%}) | {f}/24 ({f/24:.1%}) | "
            f"**{t}/24 ({t/24:.1%})** | {sel}/24 | {rel:.3f} | {ms:.0f} ms |")
    lines += [
        "",
        "对照：规则式 resolver + 工具层（无 LLM）为 **24/24 (100%)**，见 `summary.json`。",
        "",
        "> Interpretation: as the model gets stronger the SQL arm improves, so the",
        "> *absolute* gap narrows. What must be watched is whether the tool arm",
        "> stays at/near ceiling and whether tool-selection errors dominate the",
        "> residual failures. See EVAL_REPORT.md §8 for the discussion.",
        "",
    ]
    if found == 0:
        print("no llm_summary_<tag>.json files found", file=sys.stderr)
        return
    out = RESULTS / "model_scaling.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:9]))
    print(f"\n{out}")


if __name__ == "__main__":
    main()
