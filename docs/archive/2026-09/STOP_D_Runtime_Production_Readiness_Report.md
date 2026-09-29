# STOP D：Runtime Production Readiness Validation

日期：2026-09-29
范围：Agent Runtime 生命周期、状态恢复、异常降级、并发保护、Token 归因、Trace 与身份链

## 1. 验收结论

本次没有重写 Router、LangGraph 节点、SSE、checkpoint schema 或权限模型，只增加 runtime 验证矩阵，并修复了一个并发门的排队超时残留 waiter 问题。

代码级验收结果：

```ini
CHECKPOINT_RECOVERY_CONTRACT_PASS=true
POSTGRES_CHECKPOINT_PASS=true
ERROR_FALLBACK_PASS=true
RUNTIME_TRACE_PASS=true
TOKEN_ACCOUNTING_PASS=true
CONCURRENCY_SMOKE_PASS=true
REGISTRY_LAYER_GUARDS_PASS=true
GATEWAY_HEALTH_LOAD_PASS=true
```

当前最终 verdict：

```ini
STOP_D_PASS=false
RUNTIME_PRODUCTION_READY=false
```

真实 PostgreSQL checkpoint 已在明确 IPv4 地址后复验通过；当前仍未通过最终门禁的原因是高并发网关健康检查的尾延迟较高，且尚未在受控凭证下压测 `/chat/stream` 主链路。不能把只读健康端点结果直接等同于完整 Agent 请求生产就绪。

## 2. Runtime 架构边界审计

请求主链路保持：

```text
APISIX → FastAPI → GraphRunner → Router → Domain Graph
       → Tool/Skill → Reporter → SSE
```

审计确认：

- `GraphRunner` 为请求入口，启动 Trace、Memory session，并将 `RequestContext` 显式放入图状态。
- `RequestContext.checkpoint_safe()` 只输出可序列化身份与预算字段，不带 trace/sink 等运行时对象。
- 节点入口通过 `bind_from_state()` 恢复身份上下文，避免 LangGraph 线程边界丢失用户/租户信息。
- Tool 失败统一经过 `SafeToolExecutor`、ErrorMapper、Retry、CircuitBreaker、Bulkhead、Deadline。
- SSE、Router、节点 ID、前端 state contract 未修改。

身份/追踪字段映射如下：

| 语义 | 当前权威载体 |
|---|---|
| `request_id` / `trace_id` | `TraceRecord.id` / `request_id` |
| `user_id` / `tenant_id` | `RequestContext`，并注入 Trace tags |
| `conversation_id` | 请求 `session_id`；任务执行时使用 `thread_id` |
| `execution_id` | 异步任务 Trace tags 与任务租约执行上下文 |

## 3. 测试矩阵与结果

新增目录：`backend/tests/runtime/`

| 类别 | 覆盖 | 结果 |
|---|---:|---|
| checkpoint recovery | 10 cases | 通过 |
| error fallback | 20 cases | 通过 |
| runtime trace | 10 cases | 通过 |
| token accounting | 10 cases | 通过 |
| concurrency smoke | 5 个场景（10/50/100、超时、优先级） | 通过 |

新增 runtime 测试命令：

```text
cd backend
D:/Python/python.exe -m pytest tests/runtime -q --no-cov
57 passed in 14.76s
```

注册、一致性和分层守护：

```text
D:/Python/python.exe -m pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov
36 passed in 20.10s
```

## 4. Checkpoint 与状态恢复

测试使用 `MemorySaver` 构造 `first → second → last` 图：在第二个节点后中断，读取 checkpoint，验证 JSON 可序列化，再恢复执行只运行最后节点。

验证内容：

- `domain_decision`、`capability_decision`、`execution_decision` 保持不变。
- `route_mode`、`route_decision`、`query_understanding` 不漂移。
- `RequestContext` 可恢复 `user_id`、`tenant_id`、`roles`、`data_scope`。
- 恢复后的上下文 `bind_sink=False`，不会覆盖当前线程的流式 sink/trace。

既有真实 PostgreSQL checkpoint 首轮回归结果：99 个用例通过，4 个 setup error，均为：

```text
psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec
```

根因复核：测试配置使用 `localhost:5433`。Windows/psycopg 先尝试 IPv6 `::1`，约 5 秒后才落到 IPv4，连接池初始化窗口内反复等待，最终表现为 `PoolTimeout`。Docker PostgreSQL 当时 healthy，现有约 46 个 idle 连接分别来自 app/worker 的正常连接池，并非测试残留，也没有 `idle in transaction`。没有执行无目标的 `pg_terminate_backend`；宿主机测试连接数为 0，因此不存在需要强制释放的测试连接。

使用明确 IPv4 和项目端口复验：

```text
$env:PGHOST='127.0.0.1'; $env:PGPORT='5433'
D:/Python/python.exe -m pytest tests/test_task_checkpoint_recovery.py -q --no-cov
4 passed in 9.70s
```

## 5. 异常与降级矩阵

已验证：

- Connect error / connect timeout：按策略重试，成功后返回正常结果。
- Read timeout：不盲目重试，返回 `TIMEOUT`。
- HTTP 429：归一为 `RATE_LIMITED`，读取 `Retry-After`。
- HTTP 502/503：归一为 `UNAVAILABLE`，按策略耗尽重试后返回用户可理解消息。
- 400/422/403/404/参数错误：不重试并归一为参数或权限错误。
- 写操作超时：不重试，标记 `check_operation_status`，避免重复提交。
- 熔断打开：快速失败，不再访问上游。
- 隔离舱已满：返回 `TOOL_BUSY`，不会无限排队。
- Deadline 预算不足：跳过 Tool，并标记 `deadline_budget`。

本次唯一生产代码修复位于 `backend/app/api/middleware/concurrency.py`：排队超时/取消时从 heap 中移除已取消 Future，防止幽灵 waiter 累积污染队列状态。

## 6. 并发与资源保护

这是两层证据：本地受控 `_PriorityGate` smoke，以及经 APISIX `127.0.0.1:9080` 的真实只读网关健康检查负载。后者不写业务数据、不调用 LLM。

| 并发数 | P50 | P95 | P99 | error rate | active/queued 收口 |
|---:|---:|---:|---:|---:|---|
| 10 | 11.65 ms | 11.79 ms | 11.79 ms | 0% | 0 / 0 |
| 50 | 26.91 ms | 57.29 ms | 57.31 ms | 0% | 0 / 0 |
| 100 | 90.79 ms | 153.19 ms | 153.22 ms | 0% | 0 / 0 |

另验证：排队超时快速返回、high 优先级在 normal 前唤醒，且释放后 active 归零。

真实网关健康检查负载结果：

| 并发数 | P50 | P95 | P99 | error rate | 说明 |
|---:|---:|---:|---:|---:|---|
| 10 | 904.52 ms | 915.26 ms | 915.26 ms | 0% | 首轮冷请求 |
| 50 | 3895.01 ms | 5114.30 ms | 5117.52 ms | 0% | 60s 客户端窗口 |
| 100 | 7124.95 ms | 10310.96 ms | 10325.12 ms | 0% | 60s 客户端窗口 |

首轮 20s 客户端窗口下，50 并发全部 ReadTimeout，但 app 日志最终均返回 200；延长窗口后 50/100 均 0 错误，说明主要问题是冷/高并发尾延迟，而非连接泄漏。

尚未执行带真实用户凭证的 `/chat/stream` 10/50/100 负载；该路径会触发 LLM 与业务链路，需单独压测窗口，记为 `DEFERRED`。

## 7. Token 与成本治理

验证了统一 usage 解析和归因：

- OpenAI 风格 `prompt_tokens/completion_tokens`。
- Anthropic 风格 `input_tokens/output_tokens`。
- `cache_read`、`reasoning` 细粒度字段。
- provider 未提供 usage 时显式为空，不伪造 token。
- Proxy per-turn 累加可汇总多次 main model 调用。
- `TokenUsageEvent` 可序列化，并携带 `trace_id`、`request_id`、`user_id`、`tenant_id`、`role`、`stage`。
- Embedding、Rerank tracker 分别记录成功、缺失 usage 和异常事件。

生产代码已有调用覆盖：main model 由 proxy 记账，embedding/rerank 由 TokenTracker 记账；tool selector、summary 等角色通过处理绑定归因。当前未伪造外部 provider 的真实价格/账单数据。

## 8. 可观测性与安全

代码审计与测试确认：

- Trace 生命周期覆盖 request/root/node/tool/LLM 关联，异常节点 span 标记 `error`，未收口 span 标记 `leaked`。
- 异步任务 trace tags 包含 `task_id`、`execution_id`、`queue`，并回填任务 trace_id。
- 现有指标已覆盖请求耗时、LLM token、LLM 失败/降级、并发 active/queued/reject、Tool timeout/failure/retry/fallback、trace finish/leak/uncovered。
- Access log 使用 trace header 和耗时；身份由 APISIX JWT → FastAPI Principal → RequestContext 传递。
- Tool authorization 继续使用 tenant scope、role/permission、data scope；本次未改变权限判断。

现有指标命名以项目既有指标为准（例如 `chat_request_total`、`chat_request_duration_seconds`、`llm_tokens_total`、`request_concurrency_active`），没有为了 STOP D 另造重复指标。

## 9. 风险与后续动作

| 风险 | 状态 | 后续 |
|---|---|---|
| PostgreSQL 测试使用 localhost 导致 IPv6 首次连接慢 | 已定位 | 测试命令固定 `PGHOST=127.0.0.1`；生产容器使用服务名不受此问题影响 |
| APISIX 健康检查高并发尾延迟 | 已观测 | 继续拆分健康检查依赖耗时；不在 STOP D 扩大架构改造 |
| APISIX + Redis + PG + LLM 真实 `/chat/stream` 10/50/100 压测 | DEFERRED | 需要受控凭证和独立压测窗口，记录 provider 限流、队列等待、PG pool、Redis 延迟 |
| 外部 provider token/cost 实账 | DEFERRED | 使用带 usage 的测试凭证执行一次受控调用，核对 llm_usage 明细 |
| 生产告警阈值与 SLO | DEFERRED | 由部署环境按现有 Prometheus 指标配置，不在 STOP D 改架构 |

## 10. 最终 Verdict

STOP D 的本地代码契约、异常治理、Trace、Token 记账和并发门验证均通过；并发门残留 waiter 已修复并回归通过。

由于完整 `/chat/stream` 真实负载尚未执行，且健康检查在 100 并发时 P99 约 10.3 秒，本报告不宣称“生产已就绪”：

```ini
STOP_D_PASS=false
RUNTIME_PRODUCTION_READY=false
```

完成上述隔离环境复验后，仅需更新本报告的真实依赖证据与 verdict，不需要继续扩大 Router 或 Runtime 架构改造范围。
