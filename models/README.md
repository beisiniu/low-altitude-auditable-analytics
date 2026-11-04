# 大模型权重与推理引擎（故意不打包）

本目录**默认不含任何大文件**。四个 GGUF 权重合计约 **7.5 GB**，`llama-server.exe` 等引擎文件约 25 MB，不方便随项目文件夹传输；但**确定性主实验完全不需要它们**，只有补充的本地 LLM 实验需要。

请在接收电脑上按下面任一方式补齐。

---

## 1. 一键下载（推荐）

在交接包根目录执行：

```bash
python tools/download_models.py --list      # 先打印全部地址
python tools/download_models.py --model 1.5b   # 最小必需，约 1.1 GB
python tools/download_models.py --all          # 四个模型 + Windows 引擎
python tools/check_env.py                     # 检查是否齐了
```

脚本会校验每个文件的字节数，已完整的文件会自动跳过；下载失败会打印可直接复制的 URL。

默认落地目录是**本交接包的 `models/`**；也可以指定：

```bash
python tools/download_models.py --all --out D:/my-llm-assets
```

非 Windows 电脑请到 `https://github.com/ggml-org/llama.cpp/releases/tag/b12110` 选对应系统的 CPU 包，用 `--engine-url <zip 地址>` 传入，或直接设置 `LOWALT_LLM_EXE`。

---

## 2. 全部下载地址与精确大小

模型源：ModelScope（国内可达，实测约 3.9 MB/s），Qwen License。

| 用途 | 文件名 | 字节数 | 地址 |
|---|---|---|---|
| 内存兜底（未用于正式实验） | `qwen2.5-0.5b-instruct-q4_k_m.gguf` | 491,400,032 | `https://modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf` |
| 第一轮 LLM 实验 | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | 1,117,320,736 | `https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| 规模消融中间档 | `qwen2.5-3b-instruct-q4_k_m.gguf` | 2,104,932,768 | `https://modelscope.cn/models/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/master/qwen2.5-3b-instruct-q4_k_m.gguf` |
| 规模消融最强档（Q3 单文件） | `qwen2.5-7b-instruct-q3_k_m.gguf` | 3,808,391,072 | `https://modelscope.cn/models/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/master/qwen2.5-7b-instruct-q3_k_m.gguf` |

推理引擎（MIT，llama.cpp build `b12110`）：

| 平台 | 文件名 | 字节数 | 地址 |
|---|---|---|---|
| Windows x64 CPU | `llama-b12110-bin-win-cpu-x64.zip` | 24,708,445 | `https://github.com/ggml-org/llama.cpp/releases/download/b12110/llama-b12110-bin-win-cpu-x64.zip` |
| 其他平台 | 到 release 页选 CPU 包 | — | `https://github.com/ggml-org/llama.cpp/releases/tag/b12110` |

> 为什么 7B 用 **Q3_K_M**：Q4_K_M 是 4.68 GB 的分卷（两个文件），开发机当时只有约 4.5 GB 可用内存；Q3 是单文件 3.81 GB，能跑通。因此报告里 7B 的结果是**偏保守的下界**。

手工下载示例：

```bash
curl -sSL -o qwen2.5-1.5b-instruct-q4_k_m.gguf \
  "https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf"
```

---

## 3. 放置位置（三种都认，不必改代码）

`project/lowalt/eval/run_llm_eval.py` 会按顺序自动查找，`LOWALT_MODEL_ROOT` 优先：

```text
① 环境变量 LOWALT_MODEL_ROOT 指向的目录
② <project>/models/
③ <仓库根>/models/          ← download_models.py 的默认落点
④ E:/lowalt-llm/             ← 原开发机路径（新电脑通常没有，会自动跳过）
```

每种布局下，**GGUF 直接放根目录或放 `models/` 子目录都行**；引擎 `llama-server.exe` 放根目录或 `bin/` 子目录都行。脚本逐个试。

也可以完全显式指定，优先级最高：

```bash
# Windows
set LOWALT_LLM_EXE=D:\llm\bin\llama-server.exe
set LOWALT_LLM_MODEL=D:\llm\qwen2.5-1.5b-instruct-q4_k_m.gguf
# Linux / macOS
export LOWALT_LLM_EXE=/opt/llm/llama-server
export LOWALT_LLM_MODEL=/opt/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

---

## 4. 启动参数（有两个坑）

`run_llm_eval.py` 已默认带上 `--no-repack -c 1024 -t 4`，不要去掉：

- **`--no-repack` 必需**：llama.cpp 这版默认开启权重重排，会额外申请约 596 MB 连续内存；内存不足时直接报 `CPU_REPACK buffer` / `GGML_ASSERT mem_buffer` 并退出。
- **可用内存 ≥ 4 GB**：开发机 15.7 GB 总量、可用 4.5 GB 时可跑 7B Q3；若只剩 1.5 GB（例如开着大型游戏客户端），连 1.5B 都加载不了。跑 7B Q4 建议 ≥ 8 GB 或 GPU。
- 服务只监听 `127.0.0.1:8081`，**不需要任何 API key，数据不出本机**。

手动启动（一般由脚本自动完成）：

```bash
llama-server.exe -m <模型.gguf> --host 127.0.0.1 --port 8081 -c 1024 -t 4 --no-repack
```

---

## 5. 各模型的实测用途与耗时（CPU）

| 模型 | 用于 | 平均每题 | 24 题三臂全量 |
|---|---|---|---|
| 1.5B Q4 | 第一轮四臂消融 | 4.3 s | 约 6 分钟 |
| 3B Q4 | 规模消融 | 8.3 s | 约 9 分钟 |
| 7B Q3 | 规模消融 | 17.1 s | 约 22 分钟 |

完整命令、结果文件与局限见 `HANDOFF.md` 与 `project/EVAL_REPORT.md` §7–§8。
