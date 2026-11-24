# 进度与证据：RAG 与 SQL 助手链路提速

## 阶段一：生产环境基线采样与瓶颈定位（已完成）

- [x] 审计生产环境 Trace 表结构（`public.trace_store`, `public.trace_summary`, `public.sql_query_audits`）。
- [x] 获取历史 652 条 Trace 与 526 条 SQL 审计记录统计：
  - RAG Agent 历史平均时延：15.9s~16.8s，最坏达 60s~72s。
  - SQL 数据库纯执行平均时延：16.5ms，绝大部分处于 30~90ms 之间。
- [x] 生产环境真实调用实测基线：
  - SQL 接口实测端到端：
    - `在售商品有多少种？`：**8681.4ms**（DB执行 90ms）
    - `最近一个月的订单总金额是多少？`：**18799.5ms**（DB执行 38ms）
    - `查询库存最低的前5个商品名称和库存数量`：**10025.3ms**（DB执行 33ms）
  - 根因确认为云端 LLM（GLM-5.3-flash）开启了深度思考模式，单次简单 SQL 生成耗费 1530 reasoning tokens，生成阶段空耗 8~17 秒。
  - RAG 接口历史 Trace 中显示：`会话/记忆加载: 5005ms`，`长期记忆检索: 5005ms`，`记忆检索向量化: 4789ms`，主线程在 `future.result(timeout=5)` 同步硬等 5 秒超时；且请求内同一 Query 重复向量化 2~3 次。

## 阶段二：方案实施（已完成）

- [x] **优化 1：SQL 生成与表路由显式关闭 Thinking 思考模式**
  - 在 `backend/sql/sql_generator.py` 与 `backend/sql/router.py` 的 `llm.invoke` 中，解析当前模型并注入 `extra_body={"thinking": {"type": "disabled"}}` 与 `temperature=0.0`。
  - 增加对简易 mock lambda 的兼容（自动 fallback 单入参调用），避免破坏单元测试。
- [x] **优化 2：补齐 SQL 查询接口的统一 Root Trace 追踪**
  - 在 `backend/app/api/routes/sql.py` 的 `_ask_sql_agent` 中，当无 active trace 时自动初始化以 `sql_agent` 为名称的根 Trace并在完成时 finish，让 SQL 助手的各个执行阶段（选表、生成 SQL、执行 SQL）在 `trace_store` 和管理后台可观测。
- [x] **优化 3：RAG 长期记忆非阻塞化与超时快速降级**
  - 在 `backend/memory/service.py` 的 L3 检索加入 0.8s 快速超时降级；
  - 在 `backend/memory/manager.py` 和 `backend/rag/chain.py` 的 `_prepare` 阶段加入 1.5s 超时保护，坚决杜绝因向量化超时导致问答主链路硬卡死 5 秒。
- [x] **优化 4：RAG 请求内 Query Embedding 规范化复用**
  - 在 `backend/rag/embedding_singleton.py` 中对文本进行 `strip()` 规范化，消除前后空白字符导致的 sha256 缓存失效；
  - 在 `backend/rag/pipeline.py` 中防御性修复 `_session_has_history` 中的 `self.lc_chain` 访问。

## 阶段三：测试回归与生产对比（已完成）

- [x] 定向单元测试验证通过：
  - `backend/tests/sql/test_sql_router_examples.py`：35/35 PASSED (100%)
  - `backend/tests/rag/test_embedding_request_cache.py`：26/26 PASSED (100%)
  - `backend/tests/infra/test_capability_gate.py`：13/13 PASSED (100%)
- [x] 同步代码并热更新生产容器（`agent-app-1` 与 `agent-rag-service-1`）。
- [x] **生产环境同题基准压测对比**：

| 测试链路与问题 | 优化前端到端耗时 | 优化后端到端耗时 | 耗时下降比例 / 提速倍数 |
| :--- | :--- | :--- | :--- |
| **SQL**: 在售商品有多少种？ | **8681.4ms** | **1715.0ms ~ 2130.7ms** | **提速 75.4% ~ 80.2% (约 4~5 倍)** |
| **SQL**: 最近一个月的订单总金额是多少？ | **18799.5ms** | **1905.8ms ~ 2779.4ms** | **提速 85.2% ~ 89.9% (近 10 倍)** |
| **SQL**: 查询库存最低的前5个商品名称和库存数量 | **10025.3ms** | **2559.7ms ~ 3931.1ms** | **提速 60.8% ~ 74.5% (约 3~4 倍)** |
| **RAG**: 退换货政策与流程 | **13118.6ms** (历史平均16s) | **2126.7ms** | **提速 83.8% (约 6 倍)** |
| **RAG**: 记忆加载阶段 | **5005.0ms** (超时卡死) | **1.0ms** (快速降级/缓存) | **彻底消除 5 秒卡顿** |
| **RAG 向量复用**: 相同 Query 向量化出站 | 2~3 次真实调用 | **真实调用 1 次 (复用率 50%)** | **节省出站 RTT 300~700ms** |

## 阶段四：扩展任务登记（技术债务）

- [ ] **技术债务跟踪**：针对 `backend/rag/indexing/indexer.py`（3513 行巨型文件）进行阶段性架构拆分（单一职责原则 SRP + 管道设计模式 Pipeline），后续将建立独立专项重构任务。
