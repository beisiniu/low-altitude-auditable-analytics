# 低空物流可审计分析 Agent 二次开发

> 给一个「AI 不许自己算数、每个数字都要被程序重新验一遍」的开源分析 Agent，装上低空无人机配送的专属能力；再用 24 道题和四轮实验，**诚实地**量出「把业务口径写进代码」到底带来了什么。

基于开源项目 [gfirik/agentic-analytics](https://github.com/gfirik/agentic-analytics)（Apache-2.0）二次开发。数据来自 IEEE DataPort 深圳前海 truck-drone 公开基准实例（[DOI 10.21227/3gxb-mj63](https://dx.doi.org/10.21227/3gxb-mj63)，CC BY 4.0）。

---

## 核心结果

| 指标 | 基线（通用 SQL 工具链） | 低空化版 |
|---|---|---|
| 数值正确率（24 题） | **24/24** | **24/24** |
| 答案完整性 | 0.399 | **0.990**（+59.03 pp） |
| 可复核数字 | 24 | **242**（10.08×） |
| 复合推理类答案完整性 | 0.333 | **0.969**（+63.55 pp） |

**数值正确率没有提升，是故意的**：基线能写任意 SQL、直接执行独立答案键，从不被禁用题型。收益在可审计性、口径一致性与答案完整性，而不是靠削弱对手刷分。

接入真实本地模型后（Qwen2.5，1.5B / 3B / 7B，CPU 推理、无 API key）：

| 模型 | 模型自写 SQL | 模型选工具（由工具计算） |
|---|---|---|
| 1.5B Q4_K_M | 0/24 | **22/24** |
| 3B Q4_K_M | 2/24 | **23/24** |
| 7B Q3_K_M | **5/24** | **23/24** |
| 规则式工具层（无 LLM） | — | **24/24** |

模型变强后 SQL 臂单调改善，但工具臂稳定在 91.7%–95.8%，**差距仍有 18 题**。

完整方法、实验设计、失效分析与威胁效度见 [`docs/EVAL_REPORT.md`](docs/EVAL_REPORT.md) 与 [`docs/01_论文_可审计低空分析Agent.pdf`](docs/01_论文_可审计低空分析Agent.pdf)。

---

## 快速开始

Python ≥ 3.10，只需要两个包，**不需要模型、网络或任何 API key**：

```bash
pip install -r requirements.txt
python tools/check_env.py       # 环境自检
python tools/build_all.py       # 数据 → 12 项单测 → 24 题主评测 → 规模对比表
```

预期输出：

```text
Ran 12 tests ... OK
ALL numeric 24/24 -> 24/24 | relevance 0.399 -> 0.990 | verifiable numbers 24 -> 242
```

单独复现任一步：

```bash
python lowalt/data/build_dataset.py --out lowalt/data/out
python -m unittest discover -s lowalt/tests -v
python lowalt/eval/run_eval.py
python lowalt/eval/compare_models.py
```

---

## 这个项目做了什么

### 1. 数据接入（免登录、可哈希复现）

主数据归档需 IEEE 账号登录，配套代码仓库是 404 死链。但页面公开了一个 `build_case.py` 脚本，内含全部地理与随机参数。本项目用 `ast.literal_eval` 直接读取其中的常量，再用标准库确定性复现：

- 27 个 WGS-84 节点、2 个时变禁飞区、5 天 233 单、2097 个**明确标注为派生构造**的轨迹点
- day0 以归档种子精确复现 **52 单**，与 DataPort 摘要一致；重跑 SHA-256 不变
- 真实参数 / 按规则抽样 / 派生合成三层严格区分，见 [`docs/SOURCES.md`](docs/SOURCES.md)

### 2. 三个低空分析工具

| 工具 | 口径 |
|---|---|
| `drone_demand_aggregate` | 时间窗内 `COUNT(DISTINCT order_id)`，按 `(时段, 起飞 vertiport)` 分组 |
| `no_fly_conflict_count` | 点在 NFZ 多边形内（射线法）且时间落在激活窗口；1 次事件 = 一段连续命中 |
| `altitude_distribution` | 仅 `phase='cruise'` 点，按 15 m 层分组 |

### 3. 把几何计算纳入 SQL 验证器

上游契约规定：**function 事实必须在 `RULES` 注册镜像重算规则，否则整个 job 失败**。本项目：

- 为三个新工具注册 3 条镜像重算规则（TS 补丁见 [`lowalt/integration/`](lowalt/integration/)）
- 把几何判定结果物化成 `conflict_point_hits` / `conflict_events` 两张 DuckDB 表，让只懂 SQL 的验证器仍能 `SELECT COUNT(*)` 复核
- 明确区分**一致性**（验证器）与**正确性**（独立第二套角度和算法 + golden cases）

### 4. 四轮实验

1. 确定性主实验（零模型调用）
2. 自然语言解析器迭代：工具选择 19/24 → 24/24，8 条独立改写题验证泛化
3. 本地 LLM 四臂消融：0 → 4 → 7 → 22/24
4. 三档模型规模消融 + 编排层缺陷修正（7B 首跑 10/24 的坑，见报告 §8）

---

## 贡献边界

**没有修改**上游的模型层、core 状态机、agent、report 与既有验证规则。上游只改动：

```text
 M packages/verify/src/recompute.ts         2 处：新增 import、规则查找回退
 + packages/verify/src/lowaltitude-rules.ts 3 条镜像重算规则
```

其余全部为新增。真实 patch 与 `git status` 导出在 [`UPSTREAM_CHANGES.txt`](UPSTREAM_CHANGES.txt)。

> 注：本仓库是按「二次开发项目」组织的精简发布版（代码在根目录 `lowalt/`）。`docs/` 里两篇 PDF 与 `HANDOFF.md` 撰写于交接包布局（代码在 `project/` 子目录）；阅读时把命令里的 `project/` 前缀去掉即可，其余内容完全一致。工具脚本已同时兼容两种布局。

---

## 目录

```text
.
├── README.md / LICENSE / NOTICE / .gitignore / requirements.txt
├── UPSTREAM_CHANGES.txt     上游改动清单与真实 patch
├── lowalt/
│   ├── data/                数据接入脚本、来源声明、生成 CSV
│   ├── engine/              Fact/RULES/V2 验证器镜像、三个工具、解析器、合成探针
│   ├── eval/                24 题、确定性评测、LLM 实验、离线重放、全部结果 JSON
│   ├── tests/               12 项单测
│   └── integration/         上游 TS 补丁副本与说明
├── docs/                    论文式文章、零基础教程（md + pdf）、量化报告、简历/讲稿
├── models/README.md         大模型下载地址与使用方法（权重未随仓库分发）
└── tools/                   check_env / build_all / download_models / md2pdf
```

文档索引：

| 想看什么 | 文件 |
|---|---|
| 从零理解整个项目 | [`docs/02_零基础教程_从0读懂这个项目.pdf`](docs/02_零基础教程_从0读懂这个项目.pdf) |
| 学术化方法与结果 | [`docs/01_论文_可审计低空分析Agent.pdf`](docs/01_论文_可审计低空分析Agent.pdf) |
| 全部量化数字 | [`docs/EVAL_REPORT.md`](docs/EVAL_REPORT.md) |
| 简历描述 / 3 分钟讲稿 / 追问表 | [`docs/RESUME.md`](docs/RESUME.md) |
| 数据来源与 REAL vs DERIVED | [`docs/SOURCES.md`](docs/SOURCES.md) |
| 交接与排错 | [`docs/HANDOFF.md`](docs/HANDOFF.md) |

---

## 本地 LLM 实验（可选）

确定性实验**不需要模型**。补充实验用本地 `llama.cpp` CPU + Qwen2.5 GGUF，服务只监听 `127.0.0.1`，数据不出本机、无需 API key。

**权重未随仓库分发**（约 7.5 GB），下载地址、精确字节数、放置规则、环境变量与 `--no-repack` 参数全部写在 [`models/README.md`](models/README.md)：

```bash
python tools/download_models.py --model 1.5b
python lowalt/eval/run_llm_eval.py --start-server --model <模型路径> --tag 1p5b --max-tokens 160 --timeout 240
python lowalt/eval/replay_params.py
python lowalt/eval/compare_models.py
```

---

## 已知限制

- 上游 TypeScript 补丁未经 `bun run check` 编译验证（开发机 npm registry 不通）；Python 链路完整实测
- 数据：禁飞区与配送网络**几何零交集**，冲突恒为 0；该发现已作为数据质量结论报告，算法正确性另用明确标注的合成探针验证
- 轨迹为派生构造，非实测飞行；主归档需 IEEE 账号，本项目用免登录公开脚本参数复现
- 模型仅本地 1.5B–7B CPU 小模型，7B 为 Q3 量化（偏保守下界）；结论不推广到商用强模型
- 每题单次 `temperature=0 / seed=42` 运行，不报置信区间；工具臂带编排层而 SQL 臂没有
- 逐事实复核使聚合类耗时约 11×，数据量大需改增量复核

完整清单见 [`docs/EVAL_REPORT.md`](docs/EVAL_REPORT.md) §6、§8。

---

## 授权与归属

- 上游 `gfirik/agentic-analytics`：**Apache-2.0**，LICENSE 已保留，详见 [`NOTICE`](NOTICE)
- 数据集：IEEE DataPort `10.21227/3gxb-mj63`，**CC BY 4.0**，引用请标注 DOI 与作者
- 本仓库新增代码与文档：Apache-2.0
- 模型 / 引擎：Qwen License、llama.cpp (MIT)、DuckDB (MIT)
