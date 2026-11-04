"""One-command reproduction of every DETERMINISTIC experiment.

    python tools/build_all.py

Runs (from project/):
  1. lowalt/data/build_dataset.py     rebuild CSVs (byte-identical)
  2. unittest discover -s lowalt/tests  12 golden-case tests
  3. lowalt/eval/run_eval.py          24-question baseline vs low-altitude comparison
  4. lowalt/eval/compare_models.py    regenerate the model-scaling table from saved results

No model, no network, no API key. LLM experiments are intentionally excluded
(they need the separately downloaded weights; see HANDOFF.md).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def find_project_root() -> Path:
    """Handoff package keeps code under project/; a standalone GitHub clone
    keeps it at the repository root.  Both layouts are supported."""
    for cand in (HERE / "project", HERE):
        if (cand / "lowalt" / "eval" / "run_eval.py").exists():
            return cand
    return HERE / "project"


PROJECT = find_project_root()

STEPS = [
    ("生成数据", [sys.executable, "lowalt/data/build_dataset.py", "--out", "lowalt/data/out"]),
    ("单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "lowalt/tests"]),
    ("主评测 24 题", [sys.executable, "lowalt/eval/run_eval.py"]),
    ("模型规模对比表", [sys.executable, "lowalt/eval/compare_models.py"]),
]


def main() -> None:
    if not PROJECT.exists():
        print(f"找不到项目目录：{PROJECT}", file=sys.stderr)
        sys.exit(1)
    print("=" * 68)
    print("一键复现：数据 → 单测 → 24 题主评测 → 规模对比表")
    print(f"工作目录：{PROJECT}")
    print("=" * 68)

    for name, cmd in STEPS:
        print(f"\n>>> {name}: {' '.join(cmd[1:])}")
        proc = subprocess.run(cmd, cwd=str(PROJECT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        tail = (proc.stdout or "").strip().splitlines()[-6:]
        for line in tail:
            print("    " + line)
        if proc.returncode != 0:
            print(f"\n[FAILED] {name} 退出码 {proc.returncode}")
            print((proc.stderr or "")[-1200:])
            sys.exit(proc.returncode)
        print(f"    [ok] {name}")

    summary = json.loads(
        (PROJECT / "lowalt/eval/results/summary.json").read_text(encoding="utf-8"))
    o = summary["overall"]
    print("\n" + "=" * 68)
    print("复现完成，关键指标：")
    print(f"  数值正确率   {o['baseline']['numeric_correct']}/24 -> {o['lowalt']['numeric_correct']}/24")
    print(f"  答案完整性   {o['baseline']['relevance_mean']:.3f} -> {o['lowalt']['relevance_mean']:.3f}")
    print(f"  可复核数字   {o['baseline']['verifiable_numbers']} -> {o['lowalt']['verifiable_numbers']}")
    print(f"  失效案例     {len(summary['failures'])}")
    print("=" * 68)
    report = (PROJECT / "docs" / "EVAL_REPORT.md"
              if (PROJECT / "docs" / "EVAL_REPORT.md").exists()
              else PROJECT / "EVAL_REPORT.md")
    print(f"\n结果文件：{(PROJECT / 'lowalt/eval/results')}")
    print(f"完整报告：{report}")
    print("LLM 实验：需先 python tools/download_models.py --model 1.5b，见 models/README.md")


if __name__ == "__main__":
    main()
