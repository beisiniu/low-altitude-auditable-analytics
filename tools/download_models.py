"""Download the local LLM assets that are intentionally NOT bundled.

The handoff package excludes ~7.5 GB of model weights.  This script fetches
them from their public sources and verifies byte sizes.  It also prints every
URL so a human can download manually if a network blocks the script.

    python tools/download_models.py --list
    python tools/download_models.py --model 1.5b            # minimal, ~1.1 GB
    python tools/download_models.py --all                   # all 4 models + engine
    python tools/download_models.py --all --out D:/my-models

Target directory resolution (first match wins):
    --out  >  $LOWALT_MODEL_ROOT  >  <handoff>/models
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
MS = "https://modelscope.cn/models/{repo}/resolve/master/{file}"

# key -> (filename, expected byte size, url)
MODELS = {
    "0.5b": (
        "qwen2.5-0.5b-instruct-q4_k_m.gguf", 491_400_032,
        MS.format(repo="Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                  file="qwen2.5-0.5b-instruct-q4_k_m.gguf")),
    "1.5b": (
        "qwen2.5-1.5b-instruct-q4_k_m.gguf", 1_117_320_736,
        MS.format(repo="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                  file="qwen2.5-1.5b-instruct-q4_k_m.gguf")),
    "3b": (
        "qwen2.5-3b-instruct-q4_k_m.gguf", 2_104_932_768,
        MS.format(repo="Qwen/Qwen2.5-3B-Instruct-GGUF",
                  file="qwen2.5-3b-instruct-q4_k_m.gguf")),
    "7b": (
        "qwen2.5-7b-instruct-q3_k_m.gguf", 3_808_391_072,
        MS.format(repo="Qwen/Qwen2.5-7B-Instruct-GGUF",
                  file="qwen2.5-7b-instruct-q3_k_m.gguf")),
}

ENGINE_WIN = (
    "llama-b12110-bin-win-cpu-x64.zip", 24_708_445,
    "https://github.com/ggml-org/llama.cpp/releases/download/b12110/"
    "llama-b12110-bin-win-cpu-x64.zip")
ENGINE_RELEASES = "https://github.com/ggml-org/llama.cpp/releases/tag/b12110"


def default_out() -> Path:
    env = os.environ.get("LOWALT_MODEL_ROOT")
    return Path(env) if env else HERE / "models"


def fetch(path: Path, url: str, expected: int) -> bool:
    if path.exists() and path.stat().st_size == expected:
        print(f"  [skip] {path.name} already complete ({expected:,} B)")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [get ] {path.name}  ({expected/1e9:.2f} GB)\n         {url}")
    curl = shutil.which("curl")
    if curl:
        cmd = [curl, "-sSL", "--retry", "3", "--max-time", "1800",
               "-o", str(path), url]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"         curl failed: {proc.stderr[-200:]}")
    else:
        try:
            with urllib.request.urlopen(url, timeout=1800) as r, path.open("wb") as f:
                shutil.copyfileobj(r, f)
        except Exception as exc:  # noqa: BLE001
            print(f"         download failed: {exc}")
    if not path.exists():
        print("  [FAIL] file not created. Re-run this script or download manually.")
        return False
    if expected > 0 and path.stat().st_size != expected:
        print(f"  [FAIL] size {path.stat().st_size:,} != {expected:,}. "
              "Re-run this script or download manually.")
        return False
    print(f"  [ok  ] {path.stat().st_size:,} bytes"
          + ("" if expected > 0 else " (size not verified)"))
    return True


def unpack_engine(out: Path) -> None:
    name, size, url = ENGINE_WIN
    zpath = out / name
    if not zpath.exists():
        if not fetch(zpath, url, size):
            return
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out / "bin")
    exe = out / "bin" / "llama-server.exe"
    print(f"  [ok  ] engine extracted to {out / 'bin'} "
          f"({'llama-server.exe present' if exe.exists() else 'check binary name'})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print all URLs and exit")
    ap.add_argument("--all", action="store_true", help="download all models + engine")
    ap.add_argument("--model", choices=list(MODELS), action="append", default=[])
    ap.add_argument("--engine", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--engine-url", default=None,
                    help="custom llama.cpp release zip URL (non-Windows platforms)")
    args = ap.parse_args()

    out = Path(args.out) if args.out else default_out()
    print(f"target: {out}\n")

    if args.list:
        for k, (fn, size, url) in MODELS.items():
            print(f"[{k:4s}] {size/1e9:.2f} GB  {fn}\n        {url}")
        print(f"[engine-win] {ENGINE_WIN[1]/1e6:.1f} MB  {ENGINE_WIN[2]}")
        print(f"[engine-other] see {ENGINE_RELEASES}")
        return

    wanted = list(MODELS) if args.all else (args.model or ["1.5b"])
    ok = True
    for k in wanted:
        fn, size, url = MODELS[k]
        ok &= fetch(out / fn, url, size)

    if args.all or args.engine:
        if args.engine_url:
            z = out / "llama-engine-custom.zip"
            fetch(z, args.engine_url, 0)  # size unknown -> manual check below
            if z.exists():
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(out / "bin")
        elif platform.system() == "Windows":
            unpack_engine(out)
        else:
            print(f"\n[notice] Non-Windows platform ({platform.system()}). Download the CPU build "
                  f"for your OS from:\n  {ENGINE_RELEASES}\n"
                  "and pass --engine-url <zip-url>, or set LOWALT_LLM_EXE manually.")

    print("\nNext:")
    print("  python tools/check_env.py")
    print("  set LOWALT_MODEL_ROOT=" + str(out) + "   (or pass --model explicitly)")
    print("  cd project && python lowalt/eval/run_llm_eval.py --start-server "
          "--model <模型完整路径> --tag 1p5b --max-tokens 160 --timeout 240")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
