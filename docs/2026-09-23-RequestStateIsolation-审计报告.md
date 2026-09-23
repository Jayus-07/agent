# RequestStateIsolation 审计报告（STOP E4，2026-09-23）

范围：`backend/` 全仓「请求级/用户级可变状态挂进程单例」专项扫描。
方法：模式扫描（`self._last_*` / `self.last_answer*` / `self.current_*` /
`self.<身份|结果> =` 于 service/manager/system/pipeline/agent、模块级可变
dict/list）+ 逐命中人工核验生命周期（TTL / 清理函数 / finally / contextvar）。
排除（非缺陷）：tests、常量、不可变注册表、模型缓存、连接池、metrics
registry、tokenizer/embedding/reranker 缓存、DB/Redis client。

## 一、状态责任表（E0）

| 状态 | 生命周期 | 主键 | 存储位置 | 允许跨请求 |
|---|---|---|---|---|
| Request | 单请求 | request/trace id | contextvars（rag.context / request_context）、局部变量 | 否 |
| Graph | 单 graph/thread | thread_id（主图每轮唯一） | LangGraph checkpoint | 仅同 thread resume/interrupt |
| Conversation | 多聊天轮 | tenant+user+conversation 三元组 | ConversationContextStore（进程内 TTL+LRU） | 是（同会话） |
| Memory | 长期用户 | tenant+user（L2 会话行 / L3 user_id 列过滤） | PostgreSQL | 是（同用户） |
| Runtime | 进程 | 无用户身份 | 单例（模型 client/池/注册表/缓存） | 不得存请求结果 |

逐对象结论：
- **OrchestratorState**：单轮 graph state。thread_id 每轮唯一 → 不是聊天
  跨轮载体；跨轮走 ConversationContext（E1 已落地 funnel_candidates）。
- **ConversationContext**：三元组主键 + TTL(1800s) + LRU(5000) + 软失败
  同步函数模式；多 worker 不共享（进程内）——主链路同步执行于 app 进程，
  设计文档明示第一版取舍。
- **GraphRunner**：无请求态实例字段；请求中间态经 ctx 局部 dict 随生成器
  生命周期。
- **MultiAgentSystem**：进程单例（app/api/deps.py::get_multi_agent）。
  `_last_sources` 已降级 deprecated 调试兼容（E3），生产出口=AgentOutcome。
- **RAGPipeline / RAGChain**：`_last_meta/_last_faithfulness/_last_query/
  _last_sources` 均已 contextvar-backed（此前批次）；`pipeline.last_answer_meta`
  仅作 legacy 快照保留，正式出口=AskOutcome。
- **CS knowledge service**：无状态入口（身份经参数贯通，E2 已落地）。
- **direct_executor / selection_funnel / selection_decision**：无请求态
  实例字段；跨轮候选经 ConversationContext（E1）。

## 二、扫描命中分类

| 发现 | 等级 | 文件 | 状态 |
|---|---|---|---|
| `MultiAgentSystem._last_sources` 单例请求态 | P1 | orchestration/graph/system.py | **FIXED（E3，066eb36）**：AgentOutcome 随返回值带回，生产读取=0 |
| `RAGChain._last_*` / `pipeline.last_answer_meta` | P1（已修） | rag/chain.py、rag/pipeline.py | FIXED（前批次 d880eff）：contextvar-backed + AskOutcome |
| `chat.py::_active_stops`（session:request → Event） | SAFE | app/api/routes/chat.py | 键注册/finally 清理成对；key 含会话命名空间 |
| `rag_upload._progress_queues`（upload_id → Queue） | SAFE | app/api/routes/_rag_shared.py、rag_upload.py | `cleanup_expired_progress_queues()` TTL 清理 + 创建/轮询点调用 |
| `cs/typing_state._local_state` | SAFE | customer_service/typing_state.py | TTL 兜底（Redis SETEX 优先，降级 dict+monotonic 过期） |
| `competitor/qr_login._qr_sessions` | SAFE | competitor/qr_login.py | pop 消费 + 过期清扫循环 |
| `observability.*_cache`（TTL 缓存） | SAFE | app/api/routes/observability.py | expires_at 结构 + 过期淘汰 |
| `admin_tasks._op_last`（操作冷却时间戳） | SAFE | app/api/routes/admin_tasks.py | key=操作名（非用户身份），cooldown 语义 |
| `sys_providers._hits`（限流 deque） | SAFE | app/api/routes/sys_providers.py | 限流计数器 |
| `config/*_overrides`（管理端运行时覆盖） | SAFE | config/indexing_rules.py、config/model_roles.py | 配置治理面，非请求数据 |
| `token_counter._counter_cache` / `tool_runtime._policy_cache` / `evaluation._chat_cache` | SAFE | 各文件 | 模型/策略/评测缓存（豁免清单内） |
| `circuit_breaker._last_remote_poll` / `trace_writer._last_id` / `metrics._last_delta` | SAFE | infra/observability | 进程级游标/节流；StreamLatencyTracker 为每请求实例 |

结论：**无新增 P0/P1**。本轮 E1/E2/E3 之外不需要代码改动；未发现大范围
新架构问题。

## 三、遗留观察（登记，不立案）

1. `ConversationContextStore` 为进程内实现——多 worker/多副本部署下跨
   worker 不共享（设计文档已明示第一版取舍）；若未来引入多副本 app，
   需迁 Redis（键结构已按三元组设计，迁移面收敛在 store 单类）。
2. `pipeline.last_answer_meta` 与 `MultiAgentSystem._last_sources` 作为
   deprecated 调试属性仍被写入；建议后续大版本随调用方清零后删除。
3. CS 知识服务匿名命名空间 `cs-anon:<session>` 按 PRD 走会话隔离；如
   后续要求跨会话记忆，需引入真实登录主体（本审计不扩 scope）。
