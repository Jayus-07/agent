# Side-Effect Idempotency 生产 Runbook

> 日期：2026-09-24｜配套冻结：`SIDE_EFFECT_IDEMPOTENCY_OPERATIONAL_FROZEN=true`
> 上游设计：`docs/2026-09-24-Side-Effect-Idempotency-STOP-A-E-最终冻结报告.md`
> 运维 CLI：`backend/scripts/idempotency_ops.py`（在 app 容器内运行，见下）

---

## 0. 工具与连接速查

所有命令都显式走容器内配置（宿主机直连有 5432/5433 双 PG 误连风险）。

```bash
# 运维 CLI（status / stale / resolve / probe 相关）
docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py stale

# 生产 SQL（只读）
docker exec agent-postgres-1 psql -U postgres -d agent_memory -c "..."

# 用户自助查询 API（只能查自己的 key）
GET /api/idempotency/operations/{client_key}   # Bearer JWT

# Prometheus 指标
curl -s "http://localhost:9090/api/v1/query?query=idempotency_claim_total"
```

健康判定一句话：`stale` 返回 `total=0` + Prometheus 无 `idempotency_claim_total{result="unavailable"}`
增量 + `tasks` 无长期 RUNNING 孤儿 = 幂等子系统健康。

---

## 1. 正常状态

```sql
-- 状态分布（应：succeeded 占绝大多数，running 仅在飞任务，failed 零星）
SELECT operation, status, count(*) FROM ai.idempotency_records
GROUP BY operation, status ORDER BY operation, status;

-- IN_DOUBT 候选（必须为空；非空 → 见 §3）
SELECT count(*) FROM ai.idempotency_records
WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= now();
```

指标：`idempotency_claim_total{result=...}` 分布平稳；`unavailable` 恒 0。

## 2. Duplicate（重复请求）

判定标准（哪一种是正常去重）：

| 现象 | 判定 |
|---|---|
| 同一 `client_key` 的 ledger 行 `attempt=1`、`status=succeeded`，第二次请求返回 409/已处理 | **正常去重**（设计行为） |
| 同一 `client_key` 出现 `attempt` 反复增长且业务效果同步增长 | **异常**：replay 未生效，按 §5 排查 |
| 不同 `client_key` 各自成行 | 正常（不同 logical operation，本就不该互相 dedup） |

```sql
SELECT client_key, status, attempt, error_code, created_at, updated_at
FROM ai.idempotency_records
WHERE tenant_id='<t>' AND actor_id='<a>' AND operation='<op>'
  AND client_key='<key>';
```

注意：`attempt` 增长本身不是异常（retry/接管会 +1），**配合 effect 证据**判断
（如 `customer_service.agent_actions` 按 action_id 唯一）。

## 3. RUNNING stale（= IN_DOUBT 候选）

**先查：**

```bash
# 1) 找出全部 stale 行
docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py stale
```

```sql
-- 2) 定位 owner execution 是否仍活（tasks 表，仅任务路径）
SELECT id, status, execution_id, worker FROM tasks
WHERE execution_id = '<owner_execution_id>';
```

**绝不允许直接做的：**
- ❌ `UPDATE ... SET status='failed'` 手改 ledger 行；
- ❌ 删除 stale 行；
- ❌ 在租约未过期时 resolve（会被条件 UPDATE 拒绝，但**不要尝试**）；
- ❌ 关闭 ledger（fail-open）。

**处置 = §4 的 IN_DOUBT 流程。**

## 4. IN_DOUBT（人工裁决全流程）

背景：`running` 且租约过期 = 副作用是否发生**本地不可知**。系统已自动
保守阻断（重投/重试均返回 `IDEMPOTENCY_UNCERTAIN`，绝不盲重试），等待人工裁决。

```text
发现（§3 的 stale 查询 / [Idempotency] ERROR 日志 / Grafana-Prometheus）
→ 查 ledger（ops CLI status：tenant/actor/operation/client_key/owner/attempt/时间）
→ 查 trace（app 日志 grep key_hash 前 12 位；trace_id 关联 ai.trace_records）
→ 查 effect 证据（业务侧！）：
     CS 动作   → customer_service.agent_actions（action_id）+ confirmations.state
     email     → 邮件系统/收件箱实据
     probe     → ai.side_effect_probe 行数
     其他      → 对应 provider 后台/审计
→ 判断 effect：
     已发生   → resolve --decision executed [--result-json '<已知结果>']
     未发生   → resolve --decision not_executed
     无法判断 → 不裁决；保持阻断（fail-closed），升级人工 + provider 侧核实
→ resolve（运维 CLI，reason 必须含 operator 标识与依据）
→ replay/probe（重放一次该操作，验证 executed→缓存重放 / not_executed→安全重试）
→ 审计（值班记录 + 结构化日志 [Idempotency] event=side_effect_reconcile
        + ledger 行内 error_code=MANUAL_RESOLVED_EXECUTED / RESOLVED_NOT_EXECUTED）
```

命令模板：

```bash
docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py resolve \
  --tenant '<t>' --actor '<a>' --operation '<op>' --client-key '<key>' \
  --decision executed \
  --result-json '{"provider_ref":"..."}' \
  --reason "op:<operator> <证据一句话> (工单号)"
```

语义保证：`executed` → 行转 `succeeded`，后续一切重放复用缓存结果，effect 恒 1；
`not_executed` → 行转 `failed`，下一次执行安全接管重试，effect 恰 1。
两者都只允许作用于租约已过期的 running 行。

## 5. Store unavailable（ledger 不可用）

**系统行为 = fail-closed，是预期行为**：claim 阶段抛 `IdempotencyUnavailable`
→ 任务失败/请求 503，**副作用不会执行**。告警 `IdempotencyUnavailable`
（critical）触发。

**禁止**：绕过 ledger 直接执行副作用、临时禁用幂等开关、手写 ledger 行。

处置：查 PG 连接（`docker exec agent-app-1 python -c "from backend.config.database import MEMORY_DB_CONFIG; print(MEMORY_DB_CONFIG)"` +
`docker exec agent-postgres-1 psql -c 'SELECT 1'`）→ 恢复 PG → 重试任务。
恢复后的重投由 ledger 正常接手（此前 claim 失败的行不存在，按首次执行处理）。

## 6. Worker recovery（retry / recovery / resume 的区别）

| 机制 | 触发 | 幂等语义 |
|---|---|---|
| retry（RetryPolicy/autoretry） | 任务内异常，同进程重投 | 同 task_id → 同 stable key；`attempt` 递增，effect ≤1 |
| recovery（stale sweeper / broker 重投） | worker 死亡 / 消息 unacked | **新 execution_id 接管**（owner 换发），stable key 不变；effect 后 crash → IN_DOUBT 保守阻断 |
| resume（用户/管理端续跑） | PAUSED/WAITING_USER 后恢复 | task_id 不变 → 同 stable key，已完成步骤按 ledger 重放 |

运维要点：worker 重启后短暂窗口内消息经 visibility_timeout（1950s，不可调小到
活任务时长以内）或 recovery sweeper（~3min）回来；晚到消息由 lease CAS /
ledger 状态短路，无需人工干预。

## 7. 人工 SQL 规范

- 只允许 **SELECT**；一切写 ledger 的动作必须走 `idempotency_ops.py resolve`
  （条件 UPDATE + 审计标记 + 结构化日志）。
- 禁止无 WHERE 的 UPDATE/DELETE；禁止直接改 `status`。
- 任何人工操作必须先登记：operator、tenant、key、decision、依据、工单号。
- 查询一律容器内执行（双 PG 端口坑：agent 权威库=5433 映射，脚本显式配置）。

## 8. 保留策略（当前冻结口径）

- 业务动作类行 `expires_at IS NULL` → **永久保留**（审计与重放依据）。
- 显式 TTL 行由 beat `tasks-idempotency-retention`（每日 03:30）清理，
  只删 `expires_at < now()` 的行。
- **IN_DOUBT（stale running）永不自动清理**——它是裁决对象。
- 当前无数据膨胀（180 行量级）；retention 自动化扩容仅在出现实际膨胀时立项。

## 9. 已登记 Deferred（勿顺手实现）

- IN_DOUBT admin 列表页 / 告警规则（现发现路径：stale CLI + `verifying` CLI + SQL + 日志，本轮实测可用）
- ~~resolve 未记录 operator 身份字段~~（**STOP E 已部分关闭**：CS 确认行裁决新增 `--operator`（executed 必填）+ `customer_service.audit_logs` 结构化审计；ledger 侧沿用 reason 纪律）
- provider 幂等透传协议（business-service POST / Kafka，激活前必须补，
  `PROVIDER_IDEMPOTENCY_BEFORE_ACTIVATION_REQUIRED=true`）
- alerts webhook 重复通知可容忍（best-effort 观测面）

## 10. Phase3 STOP E：verifying/executing 卡死确认行的裁决（2026-09-25 新增）

CS 域副作用结果未知时（`SideEffectOutcomeUnknown`），confirmation 行落
`verifying` 并持续占住 051 业务实体唯一守卫（`_GUARD_ACTIVE_STATES`）。
唯一合法出口 = `idempotency_ops.py` 的三个 STOP E 子命令（账本+确认行+
审计**同事务**收敛，实现见 `backend/customer_service/reconciliation.py`）：

```bash
# 1) 列出全部卡死行（verifying/executing + 账本未决标记）
docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py -- verifying

# 2) 单条完整关联链（确认行+账本行+守卫语义）
docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py \
  -- inspect-confirmation --confirmation-id <id>

# 3) 裁决（三出口；reason 必须含 operator 与依据）
docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py \
  -- resolve-confirmation --confirmation-id <id> \
     --decision executed|not_executed|unresolved \
     --operator <姓名> --reason "op:<姓名> <依据/工单号>" [--result-json '{...}']
```

裁决语义（冻结，`UNKNOWN != FAILED != SUCCESS`）：

| decision | 账本（ai.idempotency_records） | 确认行 | 守卫 | 语义 |
|---|---|---|---|---|
| `executed` | SUCCEEDED + `MANUAL_RESOLVED_EXECUTED`（后续同 key 重放结果，绝不重执行） | verifying→success | 终态策略接管 | 副作用确认已发生（须 operator 身份） |
| `not_executed` | `RESOLVED_NOT_EXECUTED`（解除 UNCERTAIN 阻断，FAILED 可接管重试） | verifying→failed | 释放 | 确认未执行，可安全重试 |
| `unresolved` | **不动** | **不动** | **持续占住** | 证据不足保持调查，只落审计 |

安全不变量（测试钉死 `tests/test_stop_e_verifying_reconciliation.py`）：
- 账本双形态（`failed+UNCERTAIN` 主形态 / stale running 崩溃窗）自动分流；
  账本无未决记录时拒绝收敛（不猜）。
- 并发/重复裁决 CAS 单胜出；跨租户游标零影响（`cross_tenant_access=0`）。
- `false_success=0 / false_failure=0 / duplicate_side_effect=0`。
- booking 域 Model C 同语义收敛：`travel.booking.reconciliation.manual_resolve`
  （订单 IN_DOUBT → BOOKED/FAILED + 账本同步，回归钉死
  `tests/travel/booking/test_stop_e_manual_resolve_ledger.py`）。
