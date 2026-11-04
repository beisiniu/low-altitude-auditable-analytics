# 上游集成：验证器镜像重算规则

## 为什么改这里

上游 `packages/verify/src/recompute.ts` 的文件头注释定义了这个项目的核心契约：

> SQL facts are re-run with their stored parameters. **Function facts are recomputed from their input facts by a registry that mirrors each analysis function; a function without an entry can't be verified, which is itself an engine bug.**

`recomputeFacts()` 里查 `RULES[fn]`，查不到就返回 `ok: false`；`verifyReport()` 的 V2 一旦有 mismatch 就抛 `VerifyError`，**整个 job 不发布任何报告**（fail closed）。`Fact.computation` 的 schema 是 `{kind: "sql"|"function", sql, params, fn, inputs}`。

所以「新增一个分析函数却不给验证器注册镜像规则」在这个框架里不是遗漏，而是被架构主动禁止的。**这是本项目二次开发最关键的落点**：不是加三个函数就完事，而是让三个函数的每个输出数字都能被独立重算。

## 真实改动（`git diff` 可见）

```
 M packages/verify/src/recompute.ts        # +4 / -1
?? packages/verify/src/lowaltitude-rules.ts   # 新增
?? lowalt/                                  # 数据层、工具层、评测层
```

`recompute.ts` 只动了两处：

```ts
import { LOWALT_RULES } from "./lowaltitude-rules.ts";
...
const rule = RULES[fn] ?? LOWALT_RULES[fn];   // 原为 const rule = RULES[fn];
```

**没有修改模型层、core 状态机、agent、report 或任何既有规则**。这是最小侵入的扩展，也符合「只改数据层和工具层」的边界。

新文件导出 `LOWALT_RULES`，三条规则与 `lowalt/engine/audit.py` 的 `RULES` 一一对应：

| `fn` | 输入 facts | 镜像重算 |
|---|---|---|
| `lowalt.demand_peak.v1` | 每个 (时段, 起降节点) 分组的 SQL 计数 | `max(inputs)`，并校验报告里的 argmax 分组确实是最大值所在组 |
| `lowalt.nfz_conflict.v1` | 物化表 `conflict_events` 上按禁飞区的 SQL 计数 | `Σ inputs` |
| `lowalt.altitude_profile.v1` | 每个 15 m 高度层的巡航点 SQL 计数 | `max / Σ × 100` |

## 工具层与 TS 的对应关系

三个工具的可运行实现在 `lowalt/engine/tools.py`（Python，与评测 harness 同一进程）。它们按上游 `Fact` 的口径产出数字，并把几何判定**物化成 DuckDB 表**（`conflict_point_hits` / `conflict_events`），使原本只理解 SQL 的验证器能够用 SQL 重新计数。

`lowalt/engine/audit.py` 是 `recompute.ts` + `Fact` schema 的 Python 镜像，用于无人值守、零模型调用的确定性评测。同一份 `lowaltitude-rules.ts` 也复制在本目录，便于审阅 diff。

## 诚实说明：本机未完成 TS 编译验证

本机环境为 Windows + Node 22 + Bun 1.4.4，无 Docker、无 Ollama。上游 `bun install` 在下载依赖阶段持续失败（`registry requests failed, retrying`），换用 `--ignore-scripts` 与国内镜像重试亦未完成，因此**上游 CLI (`aa analyze`) 未在本机跑通，TS 改动未经 `bun run check` / vitest 编译验证**。

因此：

- 本仓库**可运行、可复现、数字已实测**的部分是 `lowalt/` 下的 Python 数据层 + 工具层 + 评测层；
- TS 侧的改动是按上游源码契约编写的**集成补丁**，逻辑与已通过 8 项单测的 Python 镜像规则逐条对应；
- 在具备完整 Bun 依赖的机器上，应执行 `bun install && bun run check` 后再合并该补丁。

这一限制已写入 `EVAL_REPORT.md` 的 Limitations，不会在简历或讲稿中隐去。
