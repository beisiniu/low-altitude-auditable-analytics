# 模型规模消融：工具层收益是否随模型变强而消失

同一 24 题、同一独立 SQL 答案键、同一 prompt、`temperature=0 / seed=42`，全部本地 llama.cpp CPU 推理，无 API key。

| 模型 | LLM 自写 SQL | 聚焦 prompt SQL | LLM 选工具 | 工具选择正确 | 答案完整性 | 平均耗时 |
|---|---|---|---|---|---|---|
| 1.5B Q4_K_M | 0/24 (0.0%) | 4/24 (16.7%) | **22/24 (91.7%)** | 22/24 | 0.955 | 4330 ms |
| 3B Q4_K_M | 2/24 (8.3%) | 5/24 (20.8%) | **23/24 (95.8%)** | 23/24 | 0.976 | 8296 ms |
| 7B Q3_K_M | 5/24 (20.8%) | 4/24 (16.7%) | **23/24 (95.8%)** | 23/24 | 0.948 | 17055 ms |

对照：规则式 resolver + 工具层（无 LLM）为 **24/24 (100%)**，见 `summary.json`。

> Interpretation: as the model gets stronger the SQL arm improves, so the
> *absolute* gap narrows. What must be watched is whether the tool arm
> stays at/near ceiling and whether tool-selection errors dominate the
> residual failures. See EVAL_REPORT.md §8 for the discussion.
