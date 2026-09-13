# 交接文档 — 2026-09-13 深夜（换会话用）

> ⚠️ **交接时刻的并行会话动态**（比本文其他内容都新）：
> - 新提交：133d9b6（**competitor 工具已拆分为四个单职责 Tool，测试已迁移**）、337c398（Planner 参数填充断言 + e2e 离线故障注入）、fb6e5f9（reporter None 占位修复）
> - 工作区在途：memory/manager.py、memory/service.py、infra/async_utils.py（**正在修 asyncpg 跨 loop teardown**）、rag_test_kb.json、customer_service/supervisor.py 等
> - 所以下面"未提交改动"清单和 competitor 修复描述可能已部分过时——**新会话先 git status + git log 对齐再动手**

## 一、会话已完成的工作（已提交）

| 提交 | 内容 |
|---|---|
| 293941e | RAG 服务化 + MCP 标准化四阶段（rag-server:8090 / mcp-service:8091 / 注册表收尾 / citation 透出） |
| c3d55e0 | Faithfulness 默认值断言修正（默认关是刻意决策） |
| 48544e0 | 三层路由补齐操作性信号（流程/怎么类 query 不再误判 vector_only） |
| 7f419c4 | Stage1 文档门控相似度兜底（keywords 为空的文档不再被整体排除） |
| f9e6dc4 | RC-013/RC-080 case 期望调整（基于夹具原文核实） |
| b796c28 | 治理 A：检索管线阶段契约 trace（Stage1/Adaptive/Rerank 驱逐留痕） |
| cd79d57 | 治理 B：复杂度信号表单一来源化 |
| a48cd00 | 治理 C：测试数据目录隔离（RAG_DATA_DIR → git 快照临时目录） |
| 另有并行会话提交 | faa35d5（ragas+我的31文件）、4140448、1972334 等 |

golden 评测现状：单独跑 = **106 通过 0 失败**（可复现）。

## 二、未完成任务（按优先级）

### 1. golden 全量套件下全挂（唯一未根因的问题）★最优先

**现象**：全量 pytest（隔离模式）下 RC-001~100 全部 `status=error, error="RAG pipeline not available"`；单跑 test_eval_golden.py 或 cs+evaluation 前缀都**不复现**（前缀 887 全过）。

**已做**：`backend/evaluation/runners/_common.py` 的 `init_rag_pipeline()` 原来静默吞掉构建异常，已加 traceback 留痕（logger.error "[RAG eval] RAGPipeline 构建失败"）。**此改动未提交**。

**下一步**：
1. 全量跑 `pytest backend/tests/ --no-cov -q --ignore=backend/tests/agents/test_planner_critique.py`（留痕日志会打出真实异常栈）
2. 怀疑方向：全量收集时前序测试（agents/customer_service）改变了某全局状态（embedding 配置 / asyncio loop / config 常量），导致 RAGPipeline() 构建抛错；`_common._rag_pipeline_error` 缓存后 198 处引用全部报错
3. 注意：cs+evaluation 前缀已排除 customer_service 单独作案的可能，可能是多文件组合效应，或当时撞上并行会话的中间编辑态（本会话曾两次撞见 chain.py 半成品状态）

**关键文件**：backend/evaluation/runners/_common.py（82-100 行 init_rag_pipeline、_rag_pipeline_error 缓存）

### 2. 未提交的两个改动（先提交再跑全量）

- `backend/tests/conftest.py`：新增 `_auto_approve_tools` autouse fixture（TOOL_APPROVAL_MODE=auto，修 competitor 9 失败 + data_collection/services 审批门 TTL 漂移）。**已验证**：competitor 单文件 200 全过
- `backend/evaluation/runners/_common.py`：上述留痕日志

```bash
git add backend/tests/conftest.py backend/evaluation/runners/_common.py
git commit -m "test: 写操作测试免审批 fixture + init_rag_pipeline 失败留痕"
```

### 3. 已修但需全量确认的（本次修复的验证）

- competitor 9 失败 → auto fixture 修复（200 全过已验证）
- data_collection 6 + services 2 失败 → 同上（根因：审批单在 PG 有 600s TTL，批准过一次后短时间重跑"侥幸通过"）
- stream_events 5 失败 → **尚未复现**（单跑/合跑都过），可能与审批门同源，全量见分晓

### 4. 已知未修的存量噪音（低优先级）

- 每次全量跑结尾的 `asyncpg RuntimeError: attached to a different loop`（backend/memory/manager.py:79 的 MemoryManager._shutdown 跨 loop 关闭）——只是 teardown 噪音，不影响用例结果；并行会话曾动过 memory/（token_budget.py），修前先对齐
- test_planner_critique.py 全量跑需 --ignore（循环导入已修但该文件与其余测试合跑仍有问题，未细查）

## 三、关键机制备忘（新会话必读）

1. **数据目录隔离**（backend/tests/conftest.py `_prepare_isolated_data_dir`）：任何 backend 导入前把 RAG_DATA_DIR 重定向到临时目录，从 git 跟踪的 data/ 文件重建快照。逃逸口 `RAG_EVAL_NO_ISOLATION=1`。
2. **评测断点缓存**：data/eval_runs/results_checkpoint.jsonl 按 case_id 缓存且无版本指纹——排查评测问题时先删它；golden fixture 已设 resume=False。
3. **审批门**：TOOL_APPROVAL_MODE 默认 required（config/__init__.py:60），审批单在 PG ai.tool_approval_requests 表、600s TTL——写操作测试"时好时坏"先想到它。
4. **两个检索器对象行为不同**：`pipeline.chunk_retriever`（CustomRetriever，裸向量）≠ `lc_chain.chunk_retriever_base`（ChunkLevelRetriever，两阶段带文档门控）。排查检索问题时先确认看的是哪条路径。
5. **有并行会话在同仓库工作**：改动前先 git status/log 对齐，防撞车。
6. 验证命令：
   - golden：`pytest backend/tests/evaluation/test_eval_golden.py --no-cov -q`（约 5 分钟，106 通过为绿）
   - 全量：`pytest backend/tests/ --no-cov -q --ignore=backend/tests/agents/test_planner_critique.py`（约 8-16 分钟）

## 四、剩余待办（本轮已明确、未排期）

- RC-080 歧义 query 的产品级处理（已移出确定性门禁，标 eval_tier=semantic；需要 query 澄清能力时再议）
- MemoryManager 跨 loop teardown 噪音（见 4）
- .dockerignore / pyproject.toml 有用户自己的未提交改动，别动
