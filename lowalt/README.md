# lowalt/ — 低空经济场景二次开发

基于 [gfirik/agentic-analytics](https://github.com/gfirik/agentic-analytics)（Apache-2.0, v0.1）的可审计分析 Agent，把通用分析能力「低空化」。上游设计：**模型不碰算术**——所有数字由 DuckDB SQL 计算，`verify` 包在报告发布前逐条重算，function 事实必须在 `RULES` 注册镜像重算规则，否则整个 job 失败。

## 贡献边界（面试时请按这张表讲）

| 层 | 是否改动 | 内容 |
|---|---|---|
| 模型层 / core 状态机 / agent / report | ❌ 未改 | 保持上游原样 |
| 验证器 | ✅ 最小侵入 | `packages/verify/src/recompute.ts` 只改 2 处（import + 规则查找 fallback），新增 `lowaltitude-rules.ts` 三条镜像规则 |
| 数据层 | ✅ 新增 | `lowalt/data/` — IEEE DataPort 前海案例公开参数 → DuckDB 确定性接入 |
| 工具层 | ✅ 新增 | `lowalt/engine/tools.py` — 3 个低空工具 + 通用基线工具链 |
| 评测层 | ✅ 新增 | `lowalt/eval/` — 24 题、独立 SQL 答案键、双链路确定性对比 |
| 测试 | ✅ 新增 | `lowalt/tests/` — 8 项 golden case（含独立第二几何实现） |

## 目录

```
lowalt/
├── data/
│   ├── SOURCES.md           数据来源 / CC BY 4.0 / REAL vs DERIVED / 口径差异
│   ├── ieee_build_case.py    原样归档的公开脚本（来源凭证）
│   ├── build_dataset.py      AST 读公开常量 → 确定性生成 CSV
│   └── out/                  5 张表 + manifest.json（行号、SHA-256、参数）
├── engine/
│   ├── audit.py              上游 Fact / RULES / V2 验证器的 Python 镜像
│   ├── tools.py              3 个低空工具 + 2 个通用基线工具 + 规则式 NL 解析器
│   └── probes.py             明确标注的合成几何探针（算法单测用）
├── eval/
│   ├── questions.yaml        24 题（聚合/空间/复合各 8）
│   ├── run_eval.py           零模型调用、确定性双链路对比
│   └── results/              comparison.json / summary.json / golden.lock.json
├── tests/
│   ├── test_geometry_golden.py       8 项几何/验证器 golden case
│   └── test_resolver_heldout.py      4 项解析器主集回归 + 8 条独立改写题
└── integration/README.md     上游 TS 补丁说明与未验证限制
```

## 三步复现

```bash
# 前置：Python >= 3.10, duckdb, pyyaml  (pip install duckdb pyyaml)
python lowalt/data/build_dataset.py --out lowalt/data/out
python -m unittest discover -s lowalt/tests -v          # 8 项 golden case
python lowalt/eval/run_eval.py                          # 24 题对比
```

三条命令都不需要模型、API key、网络或账号；重跑结果逐字节一致（`build_dataset.py` 已验证 SHA-256 不变）。第二条的 **12 项单测**覆盖：独立第二几何实现、时间窗、分段规则、验证器 fail-closed、零交集回归、以及自然语言解析器的主集回归与 held-out 改写题。

### 第二轮迭代：自然语言解析器 v2

首轮评测暴露工具选择 19/24。根因是 v1 匹配「话题名词」——「禁飞区活跃的窗口内共有多少配送订单」被"禁飞区"带去了空间工具。v2 改为识别**所求量**（订单量 / 冲突次数 / 高度层），并显式返回 `EXACT` / `AMBIGUOUS` / `UNRESOLVED` 三态：两类别得分接近时不硬猜、交给 planner；无所求量时拒绝猜测。结果 24/24 全部 EXACT，8 条独立改写的 held-out 题 8/8，且主评测三项指标完全不变。详见 `EVAL_REPORT.md` §4。

### 第三轮：真实本地 LLM 补充实验

主实验是零模型确定性回放，§5 曾把「无法证明有模型时工具层的价值」列为局限。第三轮补上：`lowalt/eval/run_llm_eval.py` 在同一 24 题、同一独立 SQL 答案键上跑四个臂，模型为本地 `llama.cpp` CPU + Qwen2.5-1.5B Q4_K_M（引擎与权重在 `E:/lowalt-llm/`，**无 API key、数据不出本机**）。

| 臂 | 数值正确率 |
|---|---|
| LLM 自己写 SQL（完整口径 prompt） | 0/24 |
| LLM 自己写 SQL（聚焦 prompt 消融，对 SQL 臂更有利） | 4/24 |
| LLM 选工具 + 仅类型校验（快照 `llm_*_typed_only.json`） | 7/24 |
| **LLM 选工具 + 类型 & 问句约束校验** | **22/24**（答案完整性 0.955、可复核数字 215） |
| 规则式 resolver + 工具层（主实验，无 LLM） | 24/24 |

最值得记住的发现：模型几乎对每题都填 `day=4`，**这是合法值，schema 校验拦不住语义幻觉**；加一层只读问句文本（不读答案、不读正确工具）的约束校验后才从 7/24 到 22/24。剩余 2 例工具选错恰好与 §4 的 v1 失效题重合。局限：1.5B 本地小模型、长 prompt 遵循弱、单次确定性运行，**结论不作泛化**。完整消融与局限见 `EVAL_REPORT.md` §7。

```bash
python lowalt/eval/run_llm_eval.py --start-server --max-tokens 160 --timeout 240
```

脚本自动拉起 / 关闭本地服务，日志写到 `E:/lowalt-llm/bin/llama-server.log`。

### 第四轮：模型规模消融（泛化检验）

换 3B / 7B 复测，看工具层收益是否随模型变强而消失：

| 模型 | LLM 自写 SQL | LLM 选工具 | 工具选择 |
|---|---|---|---|
| 1.5B Q4_K_M | 0/24 | 22/24 | 22/24 |
| 3B Q4_K_M | 2/24 | 23/24 | 23/24 |
| 7B Q3_K_M | **5/24** | 23/24 | 23/24 |

阶梯部分变平但没消失（差距仍有 18 题）。中途 7B 首跑只有 10/24，诊断出是**编排层只剪枝不补全**——prompt 里「不要编造时间窗」被 7B 过度遵守，`params` 全留空、工具退回默认全天；`normalize_tool_params()` 改成同时剪枝+补全后，用 `replay_params.py` 离线重放保存的原始模型输出回到 23/24（模型输出一字未改）。详见 `EVAL_REPORT.md` §8。

```bash
python lowalt/eval/replay_params.py
python lowalt/eval/compare_models.py     # -> results/model_scaling.md
```

> 上游根 `.gitignore` 含 `data/`，会忽略 `lowalt/data/out/` 下的 CSV。`ieee_build_case.py` 与 `build_dataset.py` 不受影响，clone 后执行第一步即可重建全部数据（刻意不把生成物塞进仓库）。

## 三个低空工具的计算口径

| 工具 | 口径 |
|---|---|
| `drone_demand_aggregate` | 需求 = 时间窗内 `COUNT(DISTINCT order_id)`，归属到轨迹 `seq=0` 记录的起飞 vertiport；按 (hour_bucket, origin_node_id) 分组；输出 total / by_hour / by_node / peak + argmax 分组 |
| `no_fly_conflict_count` | 命中 = 轨迹点 WGS-84 坐标在禁飞区多边形内（奇偶射线法）**且**时间戳落在激活窗口 `[from, to)`；1 次冲突事件 = 同一 (轨迹, 禁飞区) 内一段 seq 连续的命中；输出 events / points / minutes / by_zone / window_points |
| `altitude_distribution` | **仅巡航阶段点**计入，按 15 m 高度层 {45,60,75,90} 分组；输出各层点数、主导层、占比、巡航点占全部轨迹点比例 |

每个输出数字都对应一个 `Fact`：`sql` 事实按存储参数重跑，`function` 事实由 `RULES[fn]` 从输入 facts 重算；几何判定额外物化成 `conflict_point_hits` / `conflict_events` 表，让只会 SQL 的验证器也能复核。

## 环境与限制（如实说明）

- 本机：Windows / Node 22 / Python 3.13 / DuckDB 2.0.0 / Bun 1.4.4，**无 Docker、无 Ollama**。
- 上游 `bun install` 在当前网络下持续失败（`registry requests failed`），**上游 TS CLI 未在本机跑通**，TS 补丁未经 `bun run check` 编译验证；详见 `integration/README.md` 与 `EVAL_REPORT.md`。
- 因此可运行、数字已实测的交付是 `lowalt/` 下的 Python 链路；它是按上游 `Fact`/`RULES`/V2 契约严格镜像实现的，不是另起炉灶的独立项目。
- 主数据集 53.1 KB 归档需 IEEE 账号；本项目使用免登录的公开脚本参数，见 `data/SOURCES.md`。
