# 企业级 Tool 失败治理与降级机制 — 实施方案（2026-09-22）

## 0. 背景与根因

真实事故：整条 trace 185.1s，其中 `rag:rag.search` 183.7s。

**根因**（改造前）：
- `backend/skills/base.py` 的 `DEFAULT_TIMEOUT=60` / `DEFAULT_MAX_RETRIES=2`，
  执行循环 `wait_for(to_thread(invoke))` 最多跑 3 次 → 60×3 + 退避 ≈ 183.7s；
- `backend/observability/tracer.py::_aggregate_status` 按 "任一 span error →
  root = error" 聚合，RAG 失败后 Reporter 虽已优雅降级回答，整条 trace 仍被标 error；
- 没有 Request Deadline：单 Tool 的 timeout 是唯一约束，重试不受剩余预算约束；
- 异常分类只靠字符串匹配（`classify_error`），读超时与连接失败一律可重试。

## 1. 架构

```text
APISIX (SSE read_timeout 600s，不动 —— 连接层超时 ≠ 业务 Deadline)
   ↓
FastAPI chat.py
   ↓
RequestDeadline（30s 总预算 / 25s workflow / 5s reporter，env 可配）
   → 挂在 RequestContext.deadline，随图状态显式流动（跨线程安全）
   ↓
LangGraph Supervisor / Domain 子图（路由逻辑零改动）
   ↓
BaseSkill.execute / SQLSkill.execute（唯一 Tool 执行入口）
   ↓
SafeToolExecutor（backend/core/tool_runtime/executor.py）
   ├── Deadline        每次调用前：剩余预算装不下一次完整调用 → 直接降级
   ├── Timeout         wait_for(min(策略超时, 剩余预算))
   ├── RetryPolicy     保守规则（见 §3），受 remaining_budget 硬约束
   ├── CircuitBreaker  per-tool，CLOSED/OPEN/HALF_OPEN，5 次失败 / 30s 冷却
   ├── Bulkhead        per-tool 并发隔离，满 → 300ms 内拿不到槽位 → TOOL_BUSY
   ├── ErrorMapper     httpx/SQLAlchemy/Redis/Timeout/429/5xx → ToolStatus
   └── Metrics / Trace tool span events + agent_tool_* Prometheus 指标
   ↓
Tool / rag-service(8090) / MCP / SQL
```

原则：**Domain / LangGraph 只判断 ToolResult.status，不感知底层异常**。
上层契约（`step_results` dict：status/output/error/error_type/retries/…）完全不变，
新增可选字段 `tool_status / criticality / operation_type / error_code /
fallback_used / degraded / latency_ms / needs_verification`。

## 2. 新增 / 修改文件

### 新增 `backend/core/tool_runtime/`
| 文件 | 职责 |
|---|---|
| deadline.py | RequestDeadline（remaining_ms / is_expired / ensure_budget / effective_timeout_ms），checkpoint 安全序列化 |
| models.py | ToolStatus（8 态）/ ToolCriticality（required/important/optional）/ OperationType（read/write）/ ToolResult（含 user_friendly_message，原始异常只进 trace） |
| policy.py | ToolPolicy + DEFAULT_POLICIES 注册表 + `TOOL_POLICY_JSON` 环境覆盖 + 写操作后缀启发 |
| error_mapper.py | 底层异常 → ErrorClassification（status/error_code/retryable/retry_after_ms），永不抛出 |
| retry.py | 保守重试决策：错误可重试 ∧ attempt<policy.retries ∧ 剩余预算够；100~300ms jitter |
| circuit_breaker.py | 线程安全熔断器 + 全局注册表（per-tool 维度） |
| bulkhead.py | 跨 event-loop 安全的并发隔离舱（计数器+短轮询），满快速失败 |
| executor.py | SafeToolExecutor：CB→Bulkhead→Deadline→Timeout→Retry→Metrics 主流程 |
| metrics.py | agent_tool_* 指标桥接（soft-fail，label 只有 tool/domain/status/reason） |

### 修改
| 文件 | 修改 |
|---|---|
| skills/base.py | execute() 拆双路径：治理层（默认）+ 旧循环（TOOL_RUNTIME_ENABLED=false 回滚）；ToolResult→step_results 映射；criticality 路由（optional→skipped，important→degraded，required→failed） |
| skills/sql/skill.py | 保留自有 SQLResult 循环；默认值改走策略注册表（15s/0 次）；Deadline 检查 + degraded 标注 |
| core/request_context.py | + deadline 字段（None=后台任务不受约束），checkpoint_safe 序列化 |
| orchestration/request_context.py | dict 还原时重建 RequestDeadline |
| orchestration/graph/runner.py | 在线入口创建 RequestDeadline.started_now() |
| orchestration/graph/events.py | skill 降级/超时 → 用户友好 SSE log 文案（技术细节不下发） |
| observability/tracer.py | _aggregate_status：business_outcome（degraded/failed）优先于 span error 聚合 |
| observability/metrics.py | +8 个 agent_tool_* / agent_request_degraded 指标 |
| customer_service/confirmation.py | +VERIFYING 态（EXECUTING→VERIFYING→SUCCESS/FAILED），写操作结果未知走对账 |
| config/settings.py | BUSINESS/WORKFLOW/REPORTER_DEADLINE、TOOL_RUNTIME_ENABLED、CB/Bulkhead 全局参数、TOOL_POLICY_JSON |
| .env.example ×2 | 上述新 env 全量文档化 |

## 3. 关键策略（DEFAULT_POLICIES，全部可 env 覆盖）

| tool | timeout | retries | criticality | CB | bulkhead | fallback |
|---|---|---|---|---|---|---|
| rag.search | 15s* | 0 | important | ✓(5/30s) | 20 | rag_degraded |
| sql.query | 15s* | 0 | important | ✓ | 20 | sql_degraded |
| report.generate | 20s | 0 | important | ✓ | 10 | rag_degraded |
| web.search | 6s | 1 | optional | ✓ | 10 | skip |
| web.crawl | 15s | 0 | optional | ✓ | 5 | skip |
| refund.create / ticket.create | 8s | 0(强制) | required | ✓ | 10 | check_operation_status |
| email.send | 10s | 0(强制) | important | ✓ | 5 | check_operation_status |
| 未注册 tool | 15s | 1 | important | ✓ | 20 | — |

\* rag.search 15s：依真实 trace 数据校准（66 个历史样本：成功调用 p50=4.5s /
p90=9.3s / max=25.5s；8s 会误伤 19% 正常请求，15s 只影响 3%）。原规范建议
5~8s 偏紧，本地 pipeline.ask 含 LLM 合成是主因。仍可用 `TOOL_POLICY_JSON`
覆盖；Deadline 25s 兜底不变。sql.query 同理（ask_struct 含 LLM SQL 生成）。

## 4. 重试规则（§9）

- Connect error / ConnectTimeout / 502 / 503 → 最多快速重试 1 次（策略封顶）
- 429 → 按 Retry-After + 剩余预算决定
- Read timeout / 400 / 422 / 401 / 403 / 404 / 业务校验失败 → 不重试
- **写操作一律零自动重试**（executor 双保险强制），timeout → `needs_verification`
  + fallback=check_operation_status（配合 CS 确认态 VERIFYING）
- 退避 100~300ms jitter，禁止秒级指数退避（后台 Celery 任务不受影响）

## 5. 降级矩阵

| 场景 | 行为 |
|---|---|
| RAG 挂掉 | 8s 内超时/熔断 fast fail → criticality=important → step failed + business_outcome=degraded → Reporter 给"知识库暂不可用"说明；trace: rag span=error, root=degraded |
| SQL 挂掉 | 同上（15s 上限），走降级链 execute_degradation（sql↔rag 互换）语义不变 |
| Optional 挂掉 | step=skipped（不进依赖阻断），root trace 仍 success，SSE 提示"非关键步骤已跳过" |
| Required 挂掉 | step=failed + business_outcome=failed，Reporter 明确不可用说明，图不崩溃 |
| 写 Tool timeout | 零重试，needs_verification=true，CS 确认态 → VERIFYING 待对账 |
| Deadline 不足 | 不启动 Tool（budget check 在调用前），fallback=deadline_budget |
| 连续故障 | 熔断 OPEN 后 fast fail（不真实访问服务），30s 后 HALF_OPEN 探测 |

## 6. 与既有机制的关系
- **APISIX**：不动（SSE 600s 是连接层超时；业务 Deadline 在 FastAPI 内）。
- **Celery 后台任务**：不经过图链路/无 RequestContext.deadline → 不套 30s。
- **RAG 内部多级降级**：reranker passthrough（L1）、答案缓存（L2）已存在，保持不动。
- **CS 确认状态机**：仅新增 VERIFYING 态与转换，不破坏既有转换表。
- **紧急回滚**：`TOOL_RUNTIME_ENABLED=false` 一键退回旧执行循环。

## 7. 测试
- `backend/tests/tool_runtime/test_governance.py`：ErrorMapper 重试规则 / 熔断三态与 per-tool 隔离 / 隔离舱快速失败 / Deadline 预算 / 写操作零重试
- `backend/tests/tool_runtime/test_regression_rag_180s.py`：**§23 事故回归**——rag.search 无响应 → <1s 确认不可用（旧 183.7s）、root=degraded、optional 失败不拖垮 workflow
- 既有 `tests/skills`、`tests/orchestration`、`tests/observability`、`tests/customer_service` 全量回归通过（4 个存量失败为测试/代码漂移，与本改造无关，见下）
