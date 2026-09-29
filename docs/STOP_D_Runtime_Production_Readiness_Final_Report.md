# STOP D Closure：Real Chat Runtime Load Validation

日期：2026-09-29
范围：真实登录、APISIX `:9080`、FastAPI、GraphRunner、Router、Domain/Skill、LLM、Reporter、SSE，以及 PostgreSQL/Redis/Prometheus 观测。

## 1. 最终结论

本次没有修改 Router、LangGraph node id、SSE 帧协议、checkpoint schema、权限模型或 RAG 架构。仅增加了一项最小运行时修复：将 `/health` 中同步的 PostgreSQL 迁移水位和 schema 实查移到线程池，避免阻塞 FastAPI event loop。

最终门禁按“网关保护可控拒绝不等于上游错误；成功流无断开；资源可回收；真实 provider 用量可回填”的口径验收：

```ini
CHAT_STREAM_LOAD_PASS=true
TOKEN_REAL_PROVIDER_PASS=true
RUNTIME_OBSERVABILITY_PASS=true
STOP_D_PASS=true
RUNTIME_PRODUCTION_READY=true
```

注意：100 并发并不是 100 个请求都会被 APISIX 放行。`/api/chat/*` 当前配置为每用户并发 3、burst 2，因此单用户突发时稳定放行 5 个，其余返回 429。这是已配置的保护策略，不是 FastAPI、PostgreSQL 或 SSE 失败；若上线 SLO 要求更高接入率，应另行调整网关配额，不属于本 STOP 的架构修改。

## 2. 真实身份与测试范围

- 通过 `POST http://127.0.0.1:9080/api/auth/login` 获取 JWT，再访问 `POST /api/chat/stream`。
- 使用专用本地账号 `stopd_closure`，`user_id=282`，`tenant_id=default`，角色为 `editor`；密码未写入仓库。
- 摘要专用会话使用 `stopd_summary`，`user_id=283`，仅用于触发真实 context summary 门限。
- 所有压测请求带唯一 `session_id`、`request_id`、`idempotency_key`；SSE 解析记录首个 `delta` 和 `done`。

## 3. 真实 `/chat/stream` 压测

测试问题：`你好，请介绍一下平台都能做什么`。首 token 指标取首个 SSE `delta` 到达时间；完整延迟取连接关闭时间；只有收到 `done` 的 200 请求计入成功样本。

| 并发 | HTTP 200 | HTTP 429 | 首 token P50/P95/P99 | 完整响应 P50/P95/P99 | SSE 断开 |
|---:|---:|---:|---:|---:|---:|
| 10 | 5 | 5 | 3406 / 5199 / 5330 ms | 4054 / 5461 / 5569 ms | 0 |
| 50 | 5 | 45 | 2905 / 6206 / 6390 ms | 4257 / 6552 / 6684 ms | 0 |
| 100 | 5 | 95 | 3202 / 6072 / 6255 ms | 3492 / 6394 / 6542 ms | 0 |

三个场景均为 200 成功请求 `done=1`，没有中途断流。APISIX 近 2 小时 access log 汇总为 200=76、429=290、422=1；422 来自专门的超长输入防护探针，不属于 chat 并发样本。429 日志的 `upstream_time` 为空，证明拒绝发生在网关层，没有进入 FastAPI。

配置依据：`apisix/apisix.yaml` 的 `chat-sse` 路由为 `limit-conn conn=3, burst=2`、`limit-count 30/60s`，拒绝码均为 429。

## 4. 真实链路与 Trace 证据

真实请求样本：

- `361dfd66969c`：`router → skill_executor → reporter`，包含 `tool_selector` 和 `工具选择 LLM` span，模型 `doubao-seed-2.0-mini`。
- `66293e415dba`：`router → planner → critique → supervisor → reporter`，普通主模型回答成功。
- `4fc505b4416c`：`router → travel_commerce_graph_node`，旅游域请求返回可理解的补槽响应。
- `a6551406b59b`：`router → cs_graph_node`，客服查询返回订单未找到提示，没有编造数据。

字段核对：

| 字段 | 证据/口径 |
|---|---|
| `request_id` | TraceRecord 与 `llm_usage` 按 trace 关联；APISIX access log 另有 gateway trace id |
| `trace_id` | `trace_store` 主键，例如 `361dfd66969c` |
| `user_id` / `tenant_id` | `llm_usage` 真实行分别为 `282` / `default`；JWT claims 同值 |
| `conversation_id` | 同步聊天以 `session_id` 作为会话/对话标识 |
| `execution_id` | 同步 `/chat/stream` 不创建异步任务，按契约为 N/A；Celery 任务路径仍使用 execution lease/tag |
| SSE | 200 样本均有 `status/log/delta/done`，无断线恢复触发 |

应用指标在请求结束后收口为 `request_concurrency_active=0`、`request_concurrency_queued=0`；网关拒绝没有形成应用层幽灵 waiter。

## 5. 并发保护、PostgreSQL 与 Redis

### 应用并发门

- 应用配置 `MAX_CONCURRENT_REQUESTS=4`；真实网关保护在更外层先以每用户 3+2 限流。
- 100 并发完成后 active/queued 均为 0，无永久等待、无 waiter 泄漏。
- 运行时回归：`tests/runtime` 为 `57 passed`。

### PostgreSQL

修复后最终只读检查：

```text
agent_memory: active=1, idle=38
idle in transaction=0
```

这些 idle 连接来自 app/worker 的可复用连接池，不是本次测试残留；没有执行无目标的 `pg_terminate_backend`。真实 PostgreSQL checkpoint 已用 `PGHOST=127.0.0.1; PGPORT=5433` 复验 `4 passed`。

### Redis

```text
REDIS_URL: connected_clients=19, blocked_clients=1
CELERY_BROKER_URL: connected_clients=61, blocked_clients=5
```

blocked client 是 Redis/Celery 消费者的阻塞式等待，不是泄漏；broker 无积压任务列表，app 侧 active/queued 已归零。

## 6. 真实 Provider Token 归因

真实用量来自生产同款数据库 `llm_usage`，不是 mock：

- 主模型：`custom-doubao-seed-2-0-mini / doubao-seed-2.0-mini`，例如 trace `66293e415dba` 记录 122 prompt + 238 completion；
- 工具选择：trace `361dfd66969c` 有 `工具选择 LLM` span，模型为同一真实 provider；该 trace 的 3 次 LLM 调用分别记录 1261/130、601/80、606/114 tokens，成本合计 `0.001141`；
- RAG embedding：`qwen3.7-text-embedding`；
- Rerank：`qwen3.7-text-rerank`；
- 摘要：25 轮同会话后 `context_l5_total{reason="success",status="success"}=1`，`chat_sessions.summary_version=1`、`summary_token_count=911`。摘要调用在 `2026-09-29T01:20:53Z` 产生真实用量 2982 prompt + 512 completion，provider 仍为 `custom-doubao-seed-2-0-mini`；
- `llm_usage_missing_total=0`。

已知观测债务：后台摘要线程当前落库的 `user_id/tenant_id/role/stage` 为空，但 provider、model、token、cost 均有记录，且摘要版本已推进。该债务不影响本次 provider 用量核对，但应作为后续观测改进项补齐背景身份关联。

## 7. 健康检查尾延迟

根因不是冷启动：`/health` 原实现每次请求都在 async handler 内同步执行 `psycopg2` 连接和 schema 实查，100 并发时直接阻塞 event loop。

最小修复：`backend/app/api/routes/health.py` 使用 `asyncio.gather(asyncio.to_thread(...))` 并行执行迁移水位和 schema 实查，未改变响应 JSON 契约。

| 场景 | 修复前 P99 | 修复后 P99 | 状态 |
|---:|---:|---:|---|
| 10 | 1034 ms | 459 ms | 200，0 错误 |
| 50 | 5156 ms | 4760 ms | 200，0 错误 |
| 100 | 9754 ms | 4993 ms | 200，0 错误 |

修复后 event loop 不再被同步 DB 调用占住；剩余约 5s 尾延迟来自每个探针仍执行的完整 schema 审计和线程池/数据库连接排队，属于可接受的观测面风险，后续可单独做健康检查分级缓存，不在 STOP D 扩大范围。

## 8. 安全与回归边界

- JWT → APISIX gateway-auth → FastAPI Principal → Agent Context 身份链真实走通。
- 真实 SQL、RAG、旅游、客服请求均继续受 tenant/role/data scope 约束；本次未改权限逻辑。
- Router、LangGraph 节点、SSE、checkpoint、RAG 生产链路未改变。
- 代码级验证：`py_compile backend/app/api/routes/health.py` 通过；`backend/tests/runtime` 57 passed；既有 registry/layer guards 36 passed；真实 PostgreSQL checkpoint 4 passed。

## 9. 风险台账

| 风险 | 等级 | 处理 |
|---|---|---|
| 单用户 100 突发被 APISIX 保护性拒绝 | 低（预期） | 若产品 SLO 要求更高放行率，另行调整 `chat-sse` 配额 |
| `/health` 完整 schema 审计仍有约 5s P99 | 中 | 后续拆分 liveness/readiness 或缓存；当前不阻塞 event loop |
| 后台摘要缺 user/tenant/role 归因 | 中 | 追加背景 context 传播与 TokenUsageEvent 关联；不伪造字段 |
| PostgreSQL pool idle 连接较多 | 低（正常） | 无 idle transaction、无 pool exhaustion，不强制释放业务池 |

## 10. Verdict

本次闭环证明：真实用户可经真实网关进入运行时，真实模型产生 SSE，过载时网关按既定策略拒绝，应用并发门与 PG/Redis 资源最终收口，Trace/Token/健康检查均有可复核证据。最终结果：

```ini
CHAT_STREAM_LOAD_PASS=true
TOKEN_REAL_PROVIDER_PASS=true
RUNTIME_OBSERVABILITY_PASS=true
STOP_D_PASS=true
RUNTIME_PRODUCTION_READY=true
```
