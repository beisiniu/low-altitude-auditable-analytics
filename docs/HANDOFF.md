# 交接文档 HANDOFF — 低空物流可审计分析 Agent 二次开发

> 目标：**把这个文件夹整体拷到另一台电脑，按本文档操作，就能继续开发、复现全部数字、继续写实验。**
> 不需要账号、不需要 API key、不需要 Docker、不需要 GPU。大模型权重因体积原因未打包，但下载地址、校验大小、放置规则与一键脚本都在包里。

---

## 1. 这个包里有什么

```text
lowalt-handoff/
├── README_先看这个.md              ← 一页总入口（先读这个）
├── HANDOFF.md                      ← 你正在读的详细交接文档
├── 01_论文_可审计低空分析Agent.md / .pdf        研究生论文形式的技术文章
├── 02_零基础教程_从0读懂这个项目.md / .pdf      零基础详细讲解（含术语表）
├── requirements.txt               Python 依赖（只有 2 个包）
├── project/                       ★ 完整项目仓库，可直接继续开发
│   ├── EVAL_REPORT.md             全部实验的量化报告（§1–§8）
│   ├── RESUME.md                  简历描述 / 3 分钟讲稿 / 追问表
│   ├── lowalt/                    二次开发新增目录（数据/工具/评测/测试）
│   ├── packages/verify/           上游验证器的最小改动（2 行 + 1 文件）
│   └── ...                        上游仓库其余内容（含 .git，可看 diff）
├── models/
│   └── README.md                  ★ 大模型下载地址、大小、放置规则、启动参数
└── tools/
    ├── md2pdf.py                 零依赖 Markdown → PDF 转换器（两篇文档的源文件）
    ├── download_models.py        ★ 一键下载模型与推理引擎
    ├── check_env.py              ★ 环境自检（到新电脑先跑这个）
    └── build_all.py              ★ 一键复现全部确定性实验
```

**未打包**：4 个 GGUF 权重（约 7.5 GB）与 llama.cpp 引擎二进制（约 25 MB）。原因与获取方式见 `models/README.md`。项目里所有 CSV、JSON 结果、文档、代码**都已包含**。

**两个传输包**（在 E 盘、与本文件夹同级）：

| 文件 | 大小 | 内容 |
|---|---|---|
| `lowalt-handoff.zip` | 约 94 MB | 完整：含 `project/.git`（可 `git status` / `git diff`）与上游 `docs/` 文档站资源 |
| `lowalt-handoff-lite.zip` | 约 10 MB | 轻量：去掉 `.git` 与上游演示资源，**代码、数据、结果、两篇文档、四个工具脚本完全一致**；贡献边界另存为 `project/UPSTREAM_CHANGES.txt`（含真实 patch） |

任意一个解压后，本文档第 2 节的三步都能跑通。局域网/网盘传输建议用 lite 版。

---

## 2. 到新电脑后的前 5 分钟

在交接包**根目录**打开终端：

```bash
# ① 装依赖（Python >= 3.10）
pip install -r requirements.txt

# ② 环境自检（数据/结果/模型/浏览器/内存/磁盘）
python tools/check_env.py

# ③ 一键复现全部确定性实验（无需模型、无需网络）
python tools/build_all.py
```

第 ③ 步会依次执行：重建数据 → 12 项单测 → 24 题主评测 → 生成三档模型对比表，最后打印关键指标。预期输出：

```text
Ran 12 tests ... OK
24 questions, 0 model calls
ALL numeric 24/24 -> 24/24 | relevance 0.399 -> 0.990 | verifiable numbers 24 -> 242
复现完成 ...
```

只要看到这几行，说明**接手环境完全正常**，可以直接继续写代码。

---

## 3. 环境要求

| 项 | 要求 | 备注 |
|---|---|---|
| Python | ≥ 3.10（开发时 3.13） | |
| Python 包 | `duckdb`、`PyYAML` | `requirements.txt` |
| 浏览器 | Chrome / Edge / Chromium | **仅**重新生成 PDF 时需要 |
| 本地 LLM 实验 | 可用内存 ≥ 4 GB，磁盘 ≥ 5 GB | 只跑确定性实验则不需要 |
| 上游 TS 编译 | Bun 1.4.x + npm registry 可达 | 非必需，见第 9 节 |

不需要：API key、IEEE 账号、网络（除下载模型/依赖外）、Docker、GPU、管理员权限。

---

## 4. 完整复现清单

### 4.1 确定性部分（零模型调用，全部已实测）

在 `project/` 目录单独执行也可：

```bash
python lowalt/data/build_dataset.py --out lowalt/data/out
python -m unittest discover -s lowalt/tests -v
python lowalt/eval/run_eval.py
python lowalt/eval/compare_models.py
```

分别产出：

| 命令 | 产出 |
|---|---|
| `build_dataset.py` | `lowalt/data/out/` 五张 CSV + `manifest.json`（含 SHA-256） |
| `unittest discover` | 12 项测试，0.5 秒内跑完 |
| `run_eval.py` | `results/comparison.json`、`summary.json`、`golden.lock.json` |
| `compare_models.py` | `results/model_scaling.md`（读已保存的三档结果，不重新推理） |

**数据是可再生的**：CSV 是生成物且被上游 `.gitignore` 的 `data/` 规则忽略，但本包已直接带上；删掉后重跑第一条命令即可，哈希不变。

### 4.2 本地 LLM 部分（需要模型）

```bash
# 在交接包根目录
python tools/download_models.py --model 1.5b     # 或 --all
python tools/check_env.py

# 显式指定模型路径最稳妥
cd project
python lowalt/eval/run_llm_eval.py --start-server ^
  --model "E:/lowalt-handoff/models/qwen2.5-1.5b-instruct-q4_k_m.gguf" ^
  --tag 1p5b --max-tokens 160 --timeout 240
```

脚本会**自动拉起并在结束后关闭** `llama-server`，只监听 `127.0.0.1:8081`；日志写到模型目录下的 `bin/llama-server.log`。固定 `temperature=0 / seed=42 / top_p=1`。

三档复现命令（耗时约 6 / 9 / 22 分钟）：

```bash
python lowalt/eval/run_llm_eval.py --start-server --model <1.5b路径> --tag 1p5b --max-tokens 160 --timeout 240
python lowalt/eval/run_llm_eval.py --start-server --model <3b路径>   --tag 3b   --max-tokens 160 --timeout 600
python lowalt/eval/run_llm_eval.py --start-server --model <7b路径>   --tag 7b   --max-tokens 160 --timeout 600
python lowalt/eval/replay_params.py       # 编排层修正后的离线重放（不重新推理）
python lowalt/eval/compare_models.py
```

> `replay_params.py` 会把当前 `llm_*_<tag>.json` 备份成 `_prereplay.json` 再重放。**如果结果已经是正确的最终版，不要重复运行它**（会用已重放的数据覆盖快照）。

### 4.3 重新生成两篇 PDF

```bash
python tools/md2pdf.py "01_论文_可审计低空分析Agent.md" "01_论文_可审计低空分析Agent.pdf" ^
  --title "面向低空物流场景的可审计分析 Agent 二次开发" ^
  --subtitle "口径固化、几何可复核性与模型规模消融" ^
  --meta "技术研究报告" "全部数字为本地实测"
```

`md2pdf.py` 是纯标准库实现，会自动找 Chrome/Edge/Chromium。改了 `.md` 后重跑即可同步 PDF。

---

## 5. 路径与环境变量（新电脑最容易踩的坑）

`run_llm_eval.py` 已做成**可移植**，按以下顺序自动找模型，不需要改代码：

```text
① 环境变量 LOWALT_MODEL_ROOT 指向的目录
② <project>/models/
③ <交接包>/models/        ← download_models.py 默认落点
④ E:/lowalt-llm/           ← 原开发机路径，新电脑通常没有，自动跳过
```

GGUF 放根目录或 `models/` 子目录都认；`llama-server.exe` 放根目录或 `bin/` 子目录都认。若都不合适，用优先级最高的显式变量：

| 变量 | 作用 |
|---|---|
| `LOWALT_MODEL_ROOT` | 模型/引擎根目录 |
| `LOWALT_LLM_EXE` | 直接指定 `llama-server` 可执行文件 |
| `LOWALT_LLM_MODEL` | 直接指定 GGUF 文件 |
| `LOWALT_LLM_URL` | 已有服务的 OpenAI 兼容端点（默认 `http://127.0.0.1:8081`） |

Windows 示例：

```bat
set LOWALT_MODEL_ROOT=D:\llm-assets
set LOWALT_LLM_EXE=D:\llm-assets\bin\llama-server.exe
```

Linux / macOS：

```bash
export LOWALT_MODEL_ROOT=/opt/llm-assets
export LOWALT_LLM_EXE=/opt/llm-assets/llama-server
```

> 若新电脑有**自己的 OpenAI 兼容端点**（例如自建 vLLM / Ollama），只要设 `LOWALT_LLM_URL` 并**不要加 `--start-server`**，脚本会直接连它。这是接商用强模型做泛化检验的最短路径。

---

## 6. 全部数字一览（接手核对用）

| 项 | 数字 |
|---|---|
| 数据 | 27 节点 / 2 禁飞区 / 5 天 233 单 / 2097 轨迹点 / 6 条拥堵段；day0 精确复现 52 单 |
| 数据局限 | 禁飞区与配送网络几何零交集（机场区 lon 113.792–113.842，轨迹点 lon ≥ 113.845），冲突恒为 0 |
| 评测 | 24 题（8 聚合 + 8 空间 + 8 复合），答案键由独立 SQL 给定 |
| 主实验 | 数值 24/24 → 24/24；答案完整性 0.399 → 0.990（+59.03 pp）；可复核数字 24 → 242（10.08×）；V2 通过率两版均 100% |
| 分层 | 聚合 0.3125→1.0000 / 8→132 facts；空间 0.5521→1.0000 / 8→20；复合 0.3333→0.9688（+63.55 pp）/ 8→90 |
| 耗时 | 基线 4.51 ms → 低空化 25.20 ms（聚合类 3.85 → 42.79 ms，约 11×） |
| 几何探针 | 精确 1 事件 / 9 命中点；bbox 2 事件 / 18 点（高估 100%）；两套算法 1681 点 100% 一致 |
| 单测 | 12 项全过 |
| 解析器 | 工具选择 19/24 → 24/24 全 EXACT；8 条改写题 8/8；EXACT/AMBIGUOUS/UNRESOLVED 三态 |
| LLM 四臂（1.5B） | 0/24 → 4/24（聚焦 prompt）→ 7/24（仅类型校验）→ 22/24（类型+问句约束）→ 24/24（规则式） |
| 编排层修正 | 7B 首跑 10/24 → 离线重放 23/24；1.5B 22/24 不变 |
| 三档规模 | SQL 臂 0 → 2 → 5/24；工具臂 22 → 23 → 23/24；工具选择 22 → 23 → 23/24；耗时 4.3 → 8.3 → 17.1 s/题 |

这些数字全部来自 `project/lowalt/eval/results/` 下的 JSON，不是文档里手写的；重跑 `build_all.py` 应得到同样结果。

---

## 7. 代码结构与贡献边界

### 7.1 上游改动（可以用 `git status` / `git diff` 验证）

```text
 M packages/verify/src/recompute.ts          ← 2 处：新增 import、规则查找回退
?? packages/verify/src/lowaltitude-rules.ts  ← 3 条镜像重算规则
?? EVAL_REPORT.md / RESUME.md / lowalt/      ← 其余全部为新增
```

**模型层、core 状态机、agent、report 与既有验证规则均未修改。**

### 7.2 `lowalt/` 目录

| 路径 | 作用 |
|---|---|
| `data/SOURCES.md` | 数据来源、CC BY 4.0、REAL vs DERIVED、4 条口径差异 |
| `data/ieee_build_case.py` | 原样归档的公开脚本（来源凭证，manifest 里有 SHA-256） |
| `data/build_dataset.py` | AST 读公开常量 → 确定性生成 5 表 |
| `data/out/` | 生成物（本包已带上） |
| `engine/audit.py` | 上游 `Fact` / `RULES` / V2 验证器的 Python 镜像 |
| `engine/tools.py` | 3 个低空工具 + 2 个基线工具 + 所求量解析器 + 参数规范化 |
| `engine/probes.py` | 明确标注的合成几何探针 |
| `eval/questions.yaml` | 24 题与 rubric |
| `eval/run_eval.py` | 确定性双链路对比（独立 SQL 答案键） |
| `eval/run_llm_eval.py` | 本地 LLM 四臂实验（自动起停服务） |
| `eval/replay_params.py` | 编排层修正后的离线重放 |
| `eval/compare_models.py` | 三档规模对比表 |
| `eval/results/` | 所有结果 JSON 与快照 |
| `tests/` | 12 项单测（几何 + 验证器 + 解析器 held-out） |
| `integration/` | 上游 TS 补丁说明与限制 |

### 7.3 三个关键设计决策（继续开发前请理解）

1. **验证器的 `RULES` 契约**：上游规定 function 事实必须有镜像重算规则，否则整个 job 失败。所以新增分析函数必须同时登记重算规则。这是二次开发的最小落点。
2. **几何结果物化**：Python 做的点包含判定写成 DuckDB 表（`conflict_point_hits` / `conflict_events`），让只懂 SQL 的验证器仍能复核计数。但 **V2 只保证一致性，不保证几何算法正确**，因此另有独立第二实现（角度和法）与 golden cases。
3. **基线公平**：基线是能写任意 SQL 的通用工具，直接执行独立答案键，从不被禁用题型。所以「数值正确率提升」不是本项目的结论，收益在可审计性与答案完整性。

---

## 8. 已知限制（接手人必须知道）

| 类别 | 限制 |
|---|---|
| 上游 TS | 开发机 `bun install` 持续 `registry requests failed`，**TS 补丁未经 `bun run check` 编译验证**；Python 链路是完整实测的 |
| 数据 | 主 53.1 KB 归档需 IEEE SAML 登录，本项目用免登录公开脚本参数复现；配套 GitHub 仓库是 404 死链 |
| 数据 | 禁飞区与网络零交集，冲突恒为 0；轨迹为明确标注的派生构造，非实测飞行；天气事实表未生成 |
| 模型 | 仅本地 1.5B–7B CPU 小模型；7B 是 Q3_K_M（内存限制），结果偏保守；不推广到商用强模型 |
| 实验 | 每题单次 `temperature=0/seed=42` 运行，不报置信区间；工具臂带编排层而 SQL 臂没有 |
| 性能 | 逐事实复核使聚合类耗时约 11×；数据量大需改增量复核 |
| 交互 | 上游是固定流水线产报告，问答式入口由评测 harness 提供，两者通过同一事实契约对齐，未改上游 CLI |

---

## 9. 未完成事项 / 下一步（按优先级）

1. **换 7B Q4 或更强模型复测** —— 检验 `0→2→5/24` 的阶梯是否继续变平。最短路径是设 `LOWALT_LLM_URL` 指向自己的 vLLM/Ollama/OpenAI 兼容端点（不加 `--start-server`）。需要 ≥8 GB 可用内存或 GPU，或提供端点 + key。
2. **构造模糊提问分布**测 `AMBIGUOUS` 态命中率与模型介入效果（当前 24 题全 EXACT、介入率 0，说明评测集措辞太规范）。
3. **把逐 fact 重算改增量/批量**，降低 11× 耗时，给出「可审计性 vs 延迟」曲线。
4. **在 npm registry 可达环境跑 `bun install && bun run check`**，验证 TS 补丁，并用 `aa analyze examples/coffee-roaster` 跑通上游默认示例。
5. **接入有真实禁飞区交集的运营数据**，在业务题上（而非仅合成探针）验证冲突检测。

---

## 10. 常见问题排查

| 现象 | 处理 |
|---|---|
| `ModuleNotFoundError: duckdb` / `yaml` | `pip install -r requirements.txt` |
| 找不到 CSV | `python lowalt/data/build_dataset.py --out lowalt/data/out` |
| `CPU_REPACK buffer` / `GGML_ASSERT mem_buffer` | 必须 `--no-repack -c 1024`（脚本已默认）；并确认可用内存 ≥ 4 GB |
| 模型加载慢 / 退出 | 看模型目录 `bin/llama-server.log`；脚本启动失败会回显日志尾部 |
| 7B 很慢 | 正常，CPU 约 17 s/题，全量约 22 分钟 |
| PDF 生成失败 | 需要 Chrome/Edge/Chromium；或直接用包里已生成好的 PDF |
| 新电脑没有 E 盘 | 设 `LOWALT_MODEL_ROOT`，或把模型放进 `<交接包>/models/`（脚本自动找） |
| 数字对不上 | 先跑 `check_env.py` + `build_all.py`，再对照 `EVAL_REPORT.md`；结果 JSON 是唯一真相 |
| 想看我们改了上游哪几行 | 在 `project/` 里 `git status`、`git diff packages/verify/src/recompute.ts` |

---

## 11. 授权与引用

| 对象 | 授权 | 出处 |
|---|---|---|
| 上游 `gfirik/agentic-analytics` | Apache-2.0 | `https://github.com/gfirik/agentic-analytics` |
| 数据集（参数来源） | CC BY 4.0 | IEEE DataPort DOI `10.21227/3gxb-mj63` |
| Qwen2.5 模型权重 | Qwen License | ModelScope `Qwen/Qwen2.5-*-Instruct-GGUF` |
| llama.cpp | MIT | `https://github.com/ggml-org/llama.cpp` |
| DuckDB | MIT | `https://duckdb.org` |

若将本工作用于论文或报告，请按 CC BY 4.0 标注数据集 DOI 与作者，并注明上游仓库。

---

## 12. 一页速查

```text
先读        README_先看这个.md
环境自检    python tools/check_env.py
全部复现    python tools/build_all.py
模型下载    python tools/download_models.py --model 1.5b   （--all 下全部）
模型说明    models/README.md
全部数字    project/EVAL_REPORT.md
简历讲稿    project/RESUME.md
零基础讲解  02_零基础教程_从0读懂这个项目.pdf
学术版      01_论文_可审计低空分析Agent.pdf
继续开发    project/  （git status 可看贡献边界）
```
