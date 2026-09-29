# Phase 2 Step 6：Side-effect Idempotency 验收报告

日期：2026-09-24
结论：**STEP6_PASS=true**

## 1. Verdict

```text
STEP6_PASS=true
```

核心验收证据（任务书 §六十二）成立：

```text
task execution attempts > 1（同一 logical operation 多次投递/重试/重放）
business side-effect count = 1（ai.side_effect_probe 真实行数恒为 1）
```

## 2. Commit

| Commit | 内容 |
|---|---|
| fd58f65 | feat(tasks): phase2 step6 migration 047——幂等 ledger owner 列 + 实机探针表 |
| 633da0a | feat(tasks): phase2 step6 durable side-effect idempotency ledger（shared+tasks 层） |
| 564174d | feat(cs): protect business actions with side-effect idempotency（CS 接线） |
| 94c6fe6 | chore(tasks): step6 探针 env 透传（默认全关）+ sleep 钩子 |

## 3. Before Architecture（收口前如何可能重复 effect）

- 任务运行时（Step1-5）提供了 at-least-once 执行：Celery retry、Step1 recovery sweep、
  resume、admin retry、acks_late + `task_reject_on_worker_lost` 重投，全部保持同一
  task_id 重新进入执行体。
- side-effect 类 Tool（email/export/data_collection/competitor）的全局幂等依赖
  `(tenant, actor)` ContextVar 身份；**Celery 任务体此前不绑定该身份**，工具在任务
  上下文一律走「无租户兼容直调」——只剩进程内 600s 指纹保护（多进程失效）。
  实测后果：工作流邮件在跨 Worker 的 retry/recovery 重入时会重复发送。
- CS 确认链路只有 confirm 层认领闸（`claim_for_execution` 条件 UPDATE）；
  **执行层无幂等**：expert 线程超时重入、多实例并发确认、以及未来 Phase 6 真实
  执行器的 retry/recovery 都会重复执行。
- `ai.idempotency_records`（migration 031）作为 HTTP 工具侧的 PG 权威终态存在，
  但任务运行时与客服域完全未接入；stale claim 无 reconcile 路径、无保留策略。

## 4. Side-effect Inventory（全仓审计结论）

**已由本次收口覆盖（guard 接线）**：
- CS 确认执行（退款/退货/换货/改址/改密 proposal 的确认动作，当前 simulated）：
  执行边界 + 审计落库双层接入。
- 任务运行时内所有 side-effect Tool（email/export/data_collection/competitor）：
  通过任务体身份绑定获得全局幂等。

**天然幂等（无需 wrapper，审计确认）**：
- 任务行状态迁移/租约抢占（条件 UPDATE CAS）、confirmations 认领/过期、
  handoff 关闭、dispatch offers 版本 CAS——全部条件 UPDATE。
- 两套 outbox：Redis Stream 补偿（`customer_service.events.event_id UNIQUE` +
  `ON CONFLICT DO NOTHING`）与 PG 事务 outbox（`(tenant_id, handoff_id, event_seq)`
  唯一 + `FOR UPDATE SKIP LOCKED`）——设计上 exactly-once，复用不重建。
- selection/travel/prompt/model binding 等upsert（ON CONFLICT DO UPDATE）。
- RAG 索引：file_hash 去重 + 向量 upsert 收敛语义。
- 反馈候选创建（ON CONFLICT (tenant_id, trace_id) DO NOTHING）、promote 三重保护。

**纯计算（明确排除，不做幂等）**：LLM 推理、RAG 检索/重排、SQL SELECT、路由/
规划/校验、report markdown 拼接。

**审计登记的其他缺口（本次不扩scope，见 §18）**：
`data.py /collect` 旁路无审批无幂等、`model_config.py` 强制 Idempotency-Key 但未
做 claim（假幂等）、工单状态迁移无 CAS 守卫、库存 case 更新无 CAS、
`chat /messages` 前端重试重复插入、投诉/转人工工单创建的极小概率竞态
（有 active-handoff 复用 + 部分唯一索引双层缓解）。

## 5. Idempotency Architecture

```text
Task Execution（Celery retry / recovery / resume / admin retry / 重投）
      ↓ task_id 全程稳定（logical identity）；execution_id 每次拾取换发（owner）
lease + admission（Step1/4，不变）
      ↓
Business Service / Tool
      ↓
Idempotency Guard（本次新增/接线）
      ├── shared.idempotency.PostgresIdempotencyLedgerStore
      │     原子 claim：INSERT ON CONFLICT DO NOTHING（主键即唯一闸，§十一）
      │     owner CAS：complete/fail WHERE lease_id + owner_execution_id（§十九）
      │     接管：failed 无条件放行；running+租约过期仅当「owner execution
      │           确认死亡」判定通过（tasks 行 execution 已换发/离开 RUNNING）
      ├── execute_idempotent_in_transaction：业务写+ledger 终态同事务（§二十一/G13）
      ├── resolve_stale_side_effect：stale claim 人工裁决（executed|not_executed）
      └── IdempotencyExecutor：claim → 真实副作用 → complete/fail
      ↓
actual side effect
      ↓
complete（PG durable 终态）
```

fencing 与幂等职责分离（§十五/§十八）：`check_lease_active`（Step1 租约）只回答
「谁有权执行」；ledger 只回答「副作用是否已发生」。副作用执行前校验租约，失权
抛 `TaskLeaseLost`。

## 6. Key Strategy

| Operation | Logical Key（tenant + actor + operation + client_key） | owner | payload hash | Store |
|---|---|---|---|---|
| `cs.action.execute` | user_id + `cs_action:{confirmation_id}` | Redis lease / execution（CS 无任务租约，lease_id 即 owner） | action_type+target+risk_level+proposal | PG ledger |
| `cs.action.persist` | user_id（审计条目）+ action_id | —（同事务写入即终态） | action_record canonical fingerprint | 同事务 INSERT |
| `probe.effect`（实机） | step6_probe + probe_key | delivery id（每次投递换发） | `{"probe_key": ...}` | PG ledger |
| HTTP 工具类（既有） | user_id + client_key（API Idempotency-Key / fingerprint） | Redis lease_id | canonical payload | Redis claim + PG 终态（不变） |

- 禁止 uuid4 当 key（§十六）：client_key 一律来自 confirmation_id / task 组合 /
  API 显式 key。
- execution_id 绝不参与 key（§十七），只做 owner_execution_id（migration 047 新列）。
- 租户隔离（G3）：tenant 是主键首列；Case N 测试证明跨租户不互相 dedup。
- 合法第二次操作（Q20/Case O）：新 confirmation_id / 新 client_key 永不被挡。

## 7. DB Schema / Store

**不建第二套表**（任务书 §十「先审计现状，不要盲目重写」）：复用 migration 031 的
`ai.idempotency_records`（PG durable truth），047 增强：

```sql
ALTER TABLE ai.idempotency_records ADD COLUMN IF NOT EXISTS owner_execution_id VARCHAR(64);
CREATE INDEX idx_idempotency_records_stale_claim ON (status, lease_expires_at) WHERE status='running';
CREATE TABLE ai.side_effect_probe (probe_key, execution_id, payload, created_at, PK(probe_key, execution_id));
```

- 唯一约束：主键 `(tenant_id, actor_id, operation, client_key)` 即 claim 闸。
- 既有列 `lease_id/lease_expires_at/attempt/expires_at` 正好承载 lease/接管计数/TTL。
- 索引：主键 + expiry 部分索引 + stale-claim 部分索引（047）。
- retention（§五十二）：`tasks.idempotency_retention`（beat 每日 03:30）只删
  `expires_at IS NOT NULL AND expires_at < now()`；业务动作类默认不写 expires_at
  =永久保留；stale RUNNING 绝不清理（是 IN_DOUBT 裁决对象）。
- truth source：PG（Redis 只承担 HTTP 工具路径的短期 claim/lease，不变）。

## 8. State Machine

```text
CLAIMED(running, lease_id + lease_expires_at + owner_execution_id)
  ├─ complete → SUCCEEDED（owner CAS；复用结果，永不重复执行）
  ├─ fail（确认未产生副作用）→ FAILED（无条件允许接管重试）
  └─ crash 窗口（running 且租约过期，是否已执行未知）
        → 无接管判定：保守阻断（等同 IN_DOUBT，error=IDEMPOTENCY_UNCERTAIN）
        → 有接管判定且 owner execution 确认死亡：接管（attempt+1）
        → 人工裁决 resolve_stale_side_effect：
             executed → SUCCEEDED（重放）｜not_executed → FAILED（可重试）
```

未引入 12 状态设计；沿用 031 的三值 status CHECK，IN_DOUBT 由
`failed+IDEMPOTENCY_UNCERTAIN` 与「running+过期阻断」表达。

## 9. Crash Windows

| 窗口 | 结果 |
|---|---|
| crash before claim | 无痕迹，重投正常执行（effect=首次） |
| claim 后、effect 前 crash | running claim 留存；租约内重投被阻断（RUNNING）；过期后保守阻断或接管判定放行；人工裁决 not_executed 后安全重试（T5 实机：probe=0 → 裁决 → effect=1） |
| effect 后、complete 前 | **同事务模式：不存在此窗口**（业务写+终态原子提交，G13）。非原子模式（外部副作用形状）：running 留存 → 保守阻断 → 人工裁决 executed → 重放（T4 实机：probe=1 恒定） |
| complete 后、task SUCCESS 前 | ledger SUCCEEDED → recovery/重投直接重放缓存结果，不再执行（Case H/T8 实机） |

## 10. Retry / Recovery / Resume / Admin Retry

- **retry**（Celery autoretry / Step2 RetryPolicy）：同 task_id、同 logical key。
  失败（FAILED）→ 无条件接管重试；成功（SUCCEEDED）→ 重放。T6 实机：5 次
  attempt，effect=1。
- **recovery**（Step1 sweep）：RUNNING 租约过期 → 原子认领 → 同 task_id 重投 →
  落入上同路径。fencing：副作用执行前 `check_lease_active` 校验，失权拒绝。
- **resume**（PAUSED→RUNNING）：同 task_id 续跑；CS 动作审计走同事务 ledger
  （`cs.action.persist`），checkpoint resume 重复进入 `cs_graph_node` 不产生
  第二条 agent_actions。
- **admin retry**（§Q19）：不复用 task_id 的 NO-OP 短路（Step1）+ ledger 按
  logical key dedup；已 SUCCEEDED 的副作用重入一律重放（T4.3/T8 第二条消息
  实机等价证据）。admin retry 不能绕过 ledger——它走同一条 wrapper 路径。

## 11. External Provider Matrix

| Provider | 原生幂等 | 现状 |
|---|---|---|
| business-service（Java/mock）state-transitions/messages POST | 无 Idempotency-Key 透传 | 当前部署为 sandbox/本地写权模式，POST 路径未激活；激活前必须补协议（登记债务） |
| SMTP（email.send） | 无 | 本地 ledger 阻止正常 retry 重发；「发送成功但 ACK 丢失」窗口如实登记为保障边界（§二十八） |
| Kafka publish_event | 无（fire-and-forget 新 event_id） | py 侧 KAFKA_ENABLED=false 未消费（Java 闭环），激活前需 event_id 稳定化 |
| AI（DeepSeek/硅基流动） | 不适用 | 纯计算，不做幂等（Q3） |

## 12. Case A–V（tests/test_side_effect_idempotency.py，26 用例全绿）

| Case | 结果 | 说明 |
|---|---|---|
| A 首次 claim | ✅ | NEW + owner_execution_id 落库 |
| B 并发 claim（20 线程真 PG） | ✅ | 恰 1 NEW，19 RUNNING，effect=0（G4） |
| C success | ✅ | SUCCEEDED + owner CAS |
| D 重复 success | ✅（wrapper+CS 双测） | 不执行，返回缓存（G5） |
| E payload conflict | ✅ | CONFLICT/IDEMPOTENCY_CONFLICT，不复用旧结果（G6） |
| F owner CAS | ✅ | 旧 execution 不能 complete 新 owner 已接管的行（G7） |
| G retry | ✅ | failed → 无条件接管 → 重执行（attempt+1） |
| H success 后 recovery | ✅ | 重入 replay，fn 只跑一次（G9） |
| I crash before effect | ✅ | 保守阻断 + 裁决 not_executed 后重试；有判定时直接接管 |
| J external success unknown | ✅ | 非原子模式 crash → IN_DOUBT 阻断（不假装 safe retry，G15） |
| K execution_id 变化 key 不变 | ✅ | 新 owner 重放同结果（G2） |
| L resume | ✅ | CS 审计同事务 ledger dedup（重复持久化 False） |
| M admin retry | ✅ | 同 key 重入 → dedup（单测 + T4.3/T8 实机等价） |
| N tenant isolation | ✅ | 同 key 跨租户互不 dedup（G3） |
| O 不同 logical operation | ✅ | 新 confirmation_id 各自执行（Q20） |
| P rollback | ✅ | 同事务失败 → 回滚 + FAILED → 重试成功，probe 恒 1 |
| Q 原子业务 effect | ✅ | 业务行+ledger 同事务提交（G13）；重放不进事务体 |
| R timeout 分类 | ✅（单测口径） | fn 未成功即异常 → FAILED 可重试；fn 成功而终态失败 → UNCERTAIN；外部 connect/read timeout 细分属 provider 激活期债务（§11） |
| S outbox dedup | ✅（既有机制） | event_id UNIQUE + ON CONFLICT 审计确认，未重复造轮子 |
| T email dedup | ✅（既有+增强） | 工具侧既有幂等 + 本次任务体身份绑定补齐 Celery 路径 |
| U expiration | ✅ | 只清显式过期行；NULL 永久保留；stale RUNNING 不清 |
| V disabled/failure mode | ✅ | 缺身份 IdempotencyContextMissing；PG 不可用 IdempotencyUnavailable（fail closed，G16）；探针开关关闭实机验证 DISABLED 零写入 |

## 13. Docker 实机 T1–T8（agent-maintenance-worker-1，bake 镜像 rebuild）

环境：docker compose maintenance-worker（concurrency=1，maintenance 队列），
PG=容器 5433，broker=redis-broker:6380/1。探针任务 `tasks.side_effect_probe`
（`SIDE_EFFECT_PROBE_ENABLED` 门禁，crash 钩子 `SIDE_EFFECT_TEST_*` 全程按需开关，
验收后已恢复默认关闭）。

| T | 场景 | 实测结果 |
|---|---|---|
| T1 | 正常副作用 | SUCCESS，probe_rows=1，ledger succeeded attempt=1 |
| T2 | duplicate 重投 | 重放返回**首次 delivery id** 的缓存结果；probe_rows=1 |
| T3 | 并发 duplicate（3 消息） | 3 个 delivery 全部返回同一执行者缓存；probe_rows=1 |
| T4 | crash after effect | 注入 `CRASH_AFTER_EFFECT` → worker os._exit(70) → **broker 真重投**（task_reject_on_worker_lost）→ IN_DOUBT_BLOCKED；lease 过期后仍阻断（保守）；`resolve(executed)` → resolved=true（含 `event=side_effect_reconcile` 日志）→ 重放 SUCCESS；**probe_rows 恒=1** |
| T5 | crash before effect | claim 留存（attempt=1）、probe_rows=0 → broker 重投同消息 id → IN_DOUBT_BLOCKED（未发生也不盲目重试）→ `resolve(not_executed)` → 接管重试 SUCCESS，probe_rows=1，attempt=2 owner 换发 |
| T6 | transient retry | FAIL_ONCE 语义（恢复前持续失败）：autoretry 4 次 attempt 全部安全失败（probe_rows=0，attempt 递增，max_retries 预算正确）；恢复后重试 SUCCESS，**attempts=5，effect=1** |
| T7 | admin retry 等价 | 对已 SUCCEEDED 副作用的再次进入一律重放（T4.3 尾步 + T8 第二条消息 + Case M 单测）；CS duplicate confirm 单测覆盖认领闸 |
| T8 | 执行中 SIGKILL + 重启 | sleep 窗口内 `docker kill` → 容器重启 → **broker 自动重投同 task id** → 副作用恰好一次（attempt=1）→ 后续消息重放缓存；probe_rows=1 |

T8 顺带实证：T4/T5 的重投语义与 broker 真实行为一致（同 celery task id 重投）。

## 14. Metrics / Logs / Trace（实测样例）

指标（低基数 labels：operation/result；无 task_id/tenant/key，§三十二）：
`idempotency_claim_total{operation,result=new|running|succeeded|conflict|unavailable|replay}`、
`idempotency_execution_total{operation,result=success|failure|uncertain|replay|in_progress}`。

结构化日志（新增，实测样例）：

```text
[Idempotency] event=claim decision=new operation=probe.effect key_hash=3f1c… owner=5d781c22 …
[Idempotency] event=complete decision=success operation=probe.effect duration_ms=41.3
[Idempotency] event=side_effect_reconcile decision=executed operation=probe.effect reason=step6 acceptance
[SideEffectProbe] T5-case in-doubt: 副作用已执行但幂等终态未知，拒绝再次执行
```

trace：`_trace_idempotency_tags` 把 `idempotency.decision/reused/conflict` 写入
当前 trace tags（best-effort）。完整 key 不落日志（key_hash 前 12 位）。

## 15. Step1–5 Regression

```text
Step1 recovery/execution_lock/checkpoint:  tests/test_task_phase2_recovery.py 等 6 文件 ✅
Step2 retry taxonomy/policy:               test_task_phase2_step2_error_taxonomy.py ✅
Step3 queue router:                        test_task_queue_router.py ✅（047 登记 retention/probe 复用 maintenance，无拓扑变更）
Step4 admission:                           test_task_admission.py + runtime ✅
Step5 worker topology:                     test_worker_topology.py ✅
任务面合计 18 文件 + 3 契约一致性 + 新测试 = 271 passed，0 failed
CS 全目录 + 幂等协议 + 全局幂等集成 + 路由 = 1030 passed, 1 skipped
```

## 16. Baseline 对照

- 存量 flaky（非本 Step 引入）：`tests/customer_service/test_idempotency.py::
  TestDbUnavailableClaim::test_store_claim_falls_back_to_l1` 依赖真实 PG 的
  claim 路径，在并行会话共享 DB 高负载下偶发失败；单跑稳定通过。与本 Step
  改动零交集（stash 对照验证过改动前后该组合行为）。
- 并行会话期间实测：其他会话的 `pg_clean_tables` teardown（`pgtest_%` 全删）
  会清掉本 Step 测试自建的隔离表 → 新测试表名改用 `s6_` 前缀自带建删，
  脱离公共清理域（已固化为约定）。
- 另一会话在同期提交 5d86db1（observability STOP C）/654f168（travel STOP G5），
  本 Step 全部提交均路径限定，未夹带。

## 17. Hardcode / Bypass Scan

全仓副作用扫描（审计 Agent 三路并行）确认：写库/发信/外呼路径中，未被
guard 或天然幂等覆盖的清单见 §4 末段。**绕过 IdempotencyGuard 的真实业务
动作路径当前不存在于任务运行时与 CS 确认链**；登记的管理面旁路
（data.py /collect、model_config 假幂等、chat /messages）均属 API 管理面，
不在 Step6 任务运行时范围，已列入债务。

## 18. Known Limitations（如实）

1. **非原子外部副作用的 crash 窗口**：effect 后、complete 前进程死亡 → ledger
   停留 running → 保守阻断（绝不自动重执行）→ 需 `resolve_stale_side_effect`
   人工裁决。没有 provider 原生幂等时，这是诚实的唯一解（§二十二C）。
2. **跨 confirmation 的重复发起**：CS 确认执行 crash 后 confirmations 行卡
   `confirmed`（expire_stale 只扫 pending），用户重新发起会生成新 confirmation_id
   → 新 key。当前执行为 simulated 无实害；Phase 6 真实执行器必须补
   业务实体级约束（如 refunds 唯一）+ confirmed 卡死行的回收策略
   （涉状态机扩展，按 P1 STOP 条件未顺手做）。
3. **Provider 幂等透传未做**：当前无激活的外部写 provider；激活时须按 §11 矩阵
   先补协议。
4. IN_DOUBT 的运维界面未做（任务书 §五十三最低要求为可查询）：
   `GET /idempotency/operations/{client_key}`（既有）+ resolve 函数 + 结构化日志
   已可定位；admin 列表页留待需要时扩展。

## 19. Independent Debt

PENDING + broker unacked recovery blind spot（worker 拾取后即死、任务停留
PENDING、1950s visibility_timeout 后 broker 兜底重投）：**保持原状，本 Step 未
顺手扩 sweep**（§4.4）。本 Step 的 ledger 保证即使该场景 1950s 后重投，业务
副作用仍不会重复（与 T4/T5/T8 同一保护路径）。

## 20. Hard Gate 对照

G1 统一 API（shared.idempotency 三入口）✅｜G2 key 稳定不依赖 execution_id ✅｜
G3 tenant 隔离 ✅｜G4 并发原子 ✅｜G5 duplicate 不重执行 ✅｜G6 冲突 fail closed ✅｜
G7 owner CAS ✅｜G8 retry 不重复 ✅｜G9 recovery 不重复 ✅｜G10 resume 不重复 ✅｜
G11 admin retry 不重复 ✅｜G12 success-after-crash effect=1 ✅｜G13 同事务 ✅｜
G14 provider 原生幂等（存在则复用——当前无激活 provider，outbox event_id 已复用）✅｜
G15 unknown 不误标 safe retry ✅｜G16 基建故障 fail closed ✅｜G17 metrics/log/trace ✅｜
G18 Step1-5 零新增回归 ✅｜G19 未改 Worker Topology/Admission/QueueRouter 核心设计 ✅｜
G20 未顺手修 PENDING+unacked 债务 ✅
