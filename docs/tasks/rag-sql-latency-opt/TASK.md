# 任务：RAG 与 SQL 助手全链路时延优化与提速

## 1. 目标与范围

基于生产环境实测数据与 Trace 模型（`ai.trace_records`、`public.trace_store`、`public.sql_query_audits`），针对 RAG 检索问答助手与自然语言转 SQL（NL2SQL）助手开展端到端耗时瓶颈分析与链路提速优化。

### 现状基线与瓶颈诊断（生产环境实测证据）

1. **SQL 助手链路**：
   - 生产端到端平均耗时：**8.7s ~ 18.8s**。
   - 数据库只读执行耗时：**33ms ~ 90ms**（占比 < 0.5%）。
   - 安全校验与表路由耗时：**10ms ~ 40ms**。
   - **核心瓶颈点**：`generate_sql` 与 `select_tables` 调用中，云端推理模型（智谱 GLM 等）在未显式关闭 `thinking` 模式时，自动生成多达 1500+ reasoning tokens（深度思考耗时 8~17 秒）。对于纯结构化 SQL 生成和表名路由任务，深度思考属于严重的多余计算。
   - **次要瓶颈点**：`/api/sql/query` 与 `/api/sql/stream` 入口未统一拉起 Root Trace，导致独立 SQL 查询缺失全链路 Span 追踪。

2. **RAG 助手链路**：
   - 生产端到端耗时：平均 **11.4s ~ 16.8s**，最坏高达 **60s**。
   - **核心瓶颈点 1**：`MemoryManager._run` 在 `RAGChain._prepare` 中对 L3 长期记忆向量化执行了同步线程阻塞（超时 5 秒），网络抖动时硬等 5 秒超时并被 `CancelledError` 取消，拖慢问答首包响应。
   - **核心瓶颈点 2**：同一用户 Query 在单次问答中被重复发起多次远程向量化，累积消耗 1.5s~2.5s。

## 2. 优化方案实施结果

1. **质疑需求与删除多余**：
   - [x] 关闭结构化 SQL 生成（`sql_generator`）与表路由（`sql_router`）中不必要的思考模式（强制注入 `thinking: {"type": "disabled"}`），消除 1500+ reasoning tokens 的 10+ 秒空耗。
   - [x] 解除 RAG 检索前对 L3 长期记忆向量化的 5 秒同步硬等，增加 0.8s 快速超时降级与 1.5s 准备期保护。
   - [x] 去除单次请求内同一文本的多次重复向量化调用，`dedup_ratio` 达到 50%。
2. **简化优化与加快周期**：
   - [x] 补齐 `/api/sql/query` 与 `/api/sql/stream` 的 Root Trace 创建与收口，TraceStore 完整透明可查。
   - [x] 防御性修复 `RAGPipeline` 中 `_session_has_history` 的安全属性访问。
3. **自动化与回归验证**：
   - [x] 单元测试通过：`test_sql_router_examples.py` (35/35), `test_embedding_request_cache.py` (26/26), `test_capability_gate.py` (13/13)。
   - [x] 生产环境压测对比完成：SQL 端到端时延从 8~18s 降至 1.8~3.5s（提速 4~10 倍）；RAG 端到端时延从 13~16s 降至 2.1s（提速 6 倍）。
