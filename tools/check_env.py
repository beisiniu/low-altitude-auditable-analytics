"""Environment self-check for the handoff package. Run this FIRST.

    python tools/check_env.py

Checks Python version, required packages, generated data, result files,
model weights, local browser (for PDF regeneration) and free resources.
Prints a PASS / WARN / FAIL report with concrete next steps.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def find_project_root() -> Path:
    """Works both in the handoff package (code under project/) and in a
    standalone GitHub clone (code at the repository root)."""
    for cand in (HERE / "project", HERE):
        if (cand / "lowalt" / "eval" / "run_eval.py").exists():
            return cand
    return HERE / "project"


PROJECT = find_project_root()
DATA = PROJECT / "lowalt" / "data" / "out"
RESULTS = PROJECT / "lowalt" / "eval" / "results"

DATA_FILES = ["nodes.csv", "no_fly_zones.csv", "orders.csv",
              "trajectory_points.csv", "congestion.csv", "manifest.json"]
RESULT_FILES = ["comparison.json", "summary.json", "golden.lock.json",
                "model_scaling.md"]

MODEL_NAMES = [
    "qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "qwen2.5-3b-instruct-q4_k_m.gguf",
    "qwen2.5-7b-instruct-q3_k_m.gguf",
]

rows: list[tuple[str, str, str]] = []


def add(status: str, item: str, detail: str) -> None:
    rows.append((status, item, detail))


def model_roots() -> list[Path]:
    roots = []
    if os.environ.get("LOWALT_MODEL_ROOT"):
        roots.append(Path(os.environ["LOWALT_MODEL_ROOT"]))
    roots += [HERE / "models", HERE.parent / "models", Path("E:/lowalt-llm/models")]
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def free_memory_gb() -> float | None:
    if platform.system() == "Windows":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullAvailPhys / (1024 ** 3)
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 ** 2)
    except OSError:
        pass
    return None


def main() -> None:
    print("=" * 68)
    print("低空物流可审计分析 Agent — 环境自检")
    print("=" * 68)

    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    add("PASS" if ok else "FAIL", "Python",
        f"{v.major}.{v.minor}.{v.micro}  (需要 >= 3.10)")
    sys.path.insert(0, str(PROJECT))

    for pkg, mod in (("duckdb", "duckdb"), ("PyYAML", "yaml")):
        spec = importlib.util.find_spec(mod)
        add("PASS" if spec else "FAIL", pkg,
            "已安装" if spec else f"缺失 → pip install {pkg}")

    missing = [f for f in DATA_FILES if not (DATA / f).exists()]
    add("PASS" if not missing else "WARN", "数据 CSV",
        "5 表 + manifest 齐全" if not missing
        else f"缺 {missing} → 运行 python lowalt/data/build_dataset.py --out lowalt/data/out")

    missing_r = [f for f in RESULT_FILES if not (RESULTS / f).exists()]
    add("PASS" if not missing_r else "WARN", "实验结果",
        "结果文件齐全（可直接阅读）" if not missing_r else f"缺 {missing_r} → 运行 build_all.py")

    found_models, found_root = [], None
    for root in model_roots():
        hits = [m for m in MODEL_NAMES if (root / m).exists()]
        if hits and not found_models:
            found_models, found_root = hits, root
    if found_models:
        add("PASS", "本地大模型", f"{len(found_models)} 个，位于 {found_root}")
    else:
        add("WARN", "本地大模型",
            "未找到（主实验不需要；跑 LLM 实验需 python tools/download_models.py --model 1.5b）")

    engines = []
    for root in model_roots():
        p = root.parent / "bin" / "llama-server.exe"
        if p.exists():
            engines.append(p)
    if os.environ.get("LOWALT_LLM_EXE"):
        engines.append(Path(os.environ["LOWALT_LLM_EXE"]))
    add("PASS" if engines else "WARN", "推理引擎",
        str(engines[0]) if engines else "未找到 llama-server.exe（仅 LLM 实验需要）")

    browsers = [r"C:/Program Files/Google/Chrome/Application/chrome.exe",
                r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"]
    has_browser = any(Path(b).exists() for b in browsers) or any(
        shutil.which(x) for x in ("google-chrome", "chromium", "msedge"))
    add("PASS" if has_browser else "WARN", "浏览器",
        "可重新生成 PDF" if has_browser else "未找到 Chrome/Edge（只在重生成 PDF 时需要）")

    mem = free_memory_gb()
    if mem is not None:
        add("PASS" if mem >= 4 else "WARN", "可用内存",
            f"{mem:.1f} GB（跑 7B 建议 >= 4 GB；跑 Q4 建议 >= 8 GB）")
    free_disk = shutil.disk_usage(str(HERE)).free / (1024 ** 3)
    add("PASS" if free_disk >= 2 else "WARN", "磁盘可用", f"{free_disk:.1f} GB")

    if (DATA / "manifest.json").exists():
        man = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
        n = sum(t["rows"] for t in man["tables"].values())
        add("PASS", "数据清单", f"{len(man['tables'])} 张表 / {n} 行，含 SHA-256")

    print()
    for status, item, detail in rows:
        print(f"  [{status:4s}] {item:12s} {detail}")
    print()
    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    print(f"  PASS {len(rows)-len(fails)-len(warns)}   WARN {len(warns)}   FAIL {len(fails)}")
    print()
    if fails:
        print("  先修复 FAIL 项，再运行：python tools/build_all.py")
        sys.exit(1)
    print("  环境可用。下一步：python tools/build_all.py")
    print("  （LLM 实验另需模型，见 models/README.md）")


if __name__ == "__main__":
    main()
