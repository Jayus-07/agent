# Side-Effect Idempotency 人工生产验收与运维收口——OPERATIONAL FROZEN 报告

日期：2026-09-24
会话：Side-Effect Idempotency Manual Production Acceptance（STOP F0→F9）
上游冻结：`97d9d0f`（STOP A-E 最终冻结，`SIDE_EFFECT_IDEMPOTENCY_PASS=true/FROZEN=true`）

---

## 1. Verdict

```text
MANUAL_SIDE_EFFECT_ACCEPTANCE_PASS=true
IDEMPOTENCY_OPERATIONAL_CLOSURE_PASS=true
SIDE_EFFECT_IDEMPOTENCY_OPERATIONAL_FROZEN=true
```

## 2. Frozen Baseline

```text
freeze commit:      97d9d0f（当前 HEAD 2c04d3d 的祖先，merge-base 实测通过）
HEAD:               2c04d3d（travel STOP J1-J8；幂等核心文件 97d9d0f..HEAD 零改动，git diff 实测）
migration:          047_side_effect_idempotency.sql applied（2026-09-23 19:18 UTC，
                    owner_execution_id/PK 三态 CHECK 在位）；本轮新增 050 applied
runtime hash:       幂等语义三文件（shared/idempotency.py=6661bfc6、tasks/side_effect.py=24b90fd3、
                    confirmation_flow.py=3bc9b2f4）在 app/maintenance-worker/agent-worker
                    三容器与 HEAD 两两一致（实测）；本轮修复后 app 重建，hash 随提交收敛
基线漂移（不阻断，已定性）：
  - agent_tasks.py/celery_app.py/queue_router.py/task_maintenance_tasks.py/schema.sql
    带并行会话 Phase3 STOP B（pending recovery）未提交增量，diff 逐行复核为纯增量，
    tasks-idempotency-retention beat 保留，幂等语义未触碰；app 容器已含该工作区版本
  - agent/report/rag-index worker 池镜像早于冻结（缺 memory.daily_decay beat 条目与
    probe T8 sleep helper，均为非语义胶水）；cs-dispatcher 的 idempotency.py 为旧副本
    且不参与 ledger 执行路径
```

## 3. Manual Scenarios（全部经真实网关/真实 worker/真实 PG）

| # | 场景 | 驱动方式 | 结果 | 关键证据 |
|---|---|---|---|---|
| 1 | 正常单次副作用 | APISIX :9080 真实登录（e2e_seidem）→ CS 锁域聊天「我要退款」→「确认」 | PASS | ledger `cs.action.execute` client_key=`cs_action:ded0158d…` succeeded attempt=1；agent_actions 首行落库（1d01d498，status=simulated）；audit_logs success 条目；双 ledger 闸门（execute+persist）双 succeeded |
| 2 | 连续点击两次 | 同一 pending 上并发双发 `POST /api/cs/confirm` | PASS | A=200 success（action_id 56f78b4c）；B=409 `IDEMPOTENCY_CONFLICT`（服务端原子认领兜底，非前端禁用）；同 session ledger 行恰 1 条 attempt=1；[CSConfirmationFlow] confirmed+success 日志恰 1 条 |
| 3 | 页面刷新/网络重试 | 成功后新 HTTP 请求重发同业务操作 + `GET /api/idempotency/operations/{key}` + 文本路径重发「确认」 | PASS | 重发=409（无二次执行）；自助查询返回 succeeded/attempt=1/has_result；ledger 行数恒 1、agent_actions 总数恒 1 |
| 4 | 任务 retry | 探针 fma-s4-a：FAIL_ONCE 注入重试耗尽（attempt=4 全失败 effect=0）→ 干净 worker 重投 | PASS | 同一 stable key（created_at 不变）attempt=5 succeeded，effect_count=1，owner be63172d→dd96802c 换发 |
| 5 | worker restart/recovery | 探针 fma-caseb：fork worker `os._exit(71)`（effect 前）→ broker 30s 真重投 → 干净 worker 接手 | PASS | 旧 execution 1fd405bd ≠ 新 cadf8d38；stable key 不变；两次 IN_DOUBT 保守阻断后裁决重试，最终 effect=1 |
| 6 | 失权/fencing | 生产 ledger 表 owner CAS 代表场景：旧 owner 租约过期→新 owner 条件接管→旧 owner complete | PASS | 旧 owner complete 被拒（`幂等 lease 不存在或已失效`）；行归属 exec-new-BBBB、attempt 2；旧 execution 无法 commit/覆写 |

record（场景1）：tenant_id=default｜actor_id=52｜operation=cs.action.execute｜
client_key=cs_action:ded0158d-f0ee-4612-84fc-d9e76cc31b76｜execution(owner)＝CS API 路径
不携带（key 即身份）｜effect=agent_actions 1d01d498 + audit success 条目｜ledger=双行 succeeded。

## 4. IN_DOUBT Drill（本轮核心人工科目）

运维问题逐条回答：

1. **从哪里知道产生了 IN_DOUBT？** 三路均实测：①`idempotency_ops.py stale`（本轮演练实查 total=1 精确命中）；②app ERROR 日志 `[SideEffectProbe] … in-doubt: 副作用已执行但幂等终态未知，拒绝再次执行`；③SQL `status='running' AND lease_expires_at<=now()`（走 partial index `idx_idempotency_records_stale_claim`，EXPLAIN 实证）。
2. **能否定位要素？** 能——status CLI 返回 tenant/actor/operation/client_key/owner_execution_id/attempt/created/updated 全要素。
3. **能否判断 effect？** 能——Case A 查 `ai.side_effect_probe` count=1 判「已发生」；Case B count=0 判「未发生」；真实业务对应 agent_actions/邮件实据/provider 后台（Runbook §4 列明各 provider 证据位）。
4. **如何 resolve？** `idempotency_ops.py resolve --decision executed|not_executed --reason "op:<operator> <依据>"`——仅允许租约过期的 running 行（演练中误对活跃租约 resolve 被**正确拒绝**，防线实证）。
5. **resolve 是否进审计？** 是——行内 `error_code=MANUAL_RESOLVED_EXECUTED / RESOLVED_NOT_EXECUTED` + 结构化日志 `[Idempotency] event=side_effect_reconcile`（含 reason 全文）。缺口：无 operator 身份字段（P2 登记，以 reason 纪律+值班记录替代）。
6. **resolve 后 replay 是否保持 exactly-once？** 是——Case A resolve(executed) 后重放返回缓存结果（fn 未再执行），effect count 恒 1；Case B resolve(not_executed) 后重试 effect 恰 1（attempt=3 三次 execution 换发、key 恒定）。

Case A（effect 已发生 → 裁决 succeeded → 重放）：effect=79b70a49 crash 后，
重投先被**活跃租约** RUNNING 冲突挡下（BLOCKED），租约过期后 IN_DOUBT 阻断，
裁决后重放返回 `{"delivery":"79b70a49","verified_by":"op:seidem-acceptance"}`，probe 恒 1。

Case B（effect 未发生 → 裁决 FAILED → 安全重试）：两次 crash（第二次为演练容器
旗标未清，客观构成独立复验）均被 IN_DOUBT 阻断，两次裁决后干净重试成功，
effect 恰 1。

## 5. Provider Inventory

| Provider | Operation | 已启用 | 原生幂等 | 当前透传 | 风险/处置 |
|---|---|---|---|---|---|
| CS 动作（模拟执行器） | cs.action.execute / cs.action.persist | 是 | n/a（进程内模拟） | ledger 全覆盖（本轮实测） | Phase 6 真实执行器接入前必须沿用本边界 |
| business-service（Java 订单/退款） | POST（只读 GET 已接） | **否**（仅查询 GET） | 未知 | 未接 | **激活门禁 PROVIDER_IDEMPOTENCY_BEFORE_ACTIVATION_REQUIRED=true**：须 Idempotency-Key/request_id 稳定透传 |
| SMTP email | email.send | 是（低频） | 协议无幂等键 | 工具侧 ledger（tenant/actor 绑定+可选 client key） | SMTP ACK 丢失=未知结局 → IN_DOUBT 人工裁决（设计内） |
| alerts webhook | 告警外推 | 否（URL 空） | 无 | 无（best-effort+冷却） | 观测面可容忍重复，P2 |
| tencent LBS / weather（travel） | GET 查询 | 是 | n/a | n/a | 纯只读，无副作用 |
| Kafka publish | 预留 | 否 | event_id 可用 | 未接 | 激活前补稳定 event_id（同上门禁） |
| DB 内部写（data.collect/rag.index.submit/tasks.create/budget/model_price 等） | 各 operation | 是 | 同事务/UNIQUE | execute_idempotent_in_transaction | 无 crash 窗口（G13），生产 ledger 178 行分布健康 |

## 6. Observability（运维如何发现异常）

- **指标（实测可查）**：`idempotency_claim_total{operation,result}`（app 进程内
  cs.action.execute new=2 与本进程内 2 次确认精确一致）、`idempotency_execution_total`
  （success/replay/failure/in_progress）；Prometheus 10 targets 全 up；四 worker :9809
  multiproc（Phase2-F 既有）。
- **告警（既有）**：`IdempotencyUnavailable`（critical，claim unavailable>0/5m）、
  `IdempotencyConflictSpike`（warning，>20/10m）。
- **缺口（P2 Deferred）**：IN_DOUBT 数量与 stale RUNNING 无指标/告警（发现走 stale CLI
  + SQL + 日志，本轮实证可用）；补告警需新增 gauge 指标，登记不扩 scope。
- **日志/trace**：`[Idempotency] event=claim/complete/side_effect_reconcile`（key_hash
  前 12 位，不含完整业务键）；`[SideEffectProbe] in-doubt` ERROR；trace tags
  idempotency.decision。

## 7. DB Operational State（验收终值）

```text
status 分布：succeeded 176 ｜ failed 4 ｜ running 0（stale=0）
IN_DOUBT：0（演练行已全部裁决收口）
tenant 隔离：PK 首列，现网 tenant=default/fma-tenant 等，无跨租户 dedup 实例
expires/retention：expires_at 非空行=0（业务行全部永久保留）；retention beat
    每日 03:30 只清显式 TTL 过期行；stale running 绝不自动清（代码+实据双证）
索引：stale 查询走 idx_idempotency_records_stale_claim、key 查询走 PK（EXPLAIN 实证），
    无需新增索引
```

## 8. Runbook

`docs/2026-09-24-Side-Effect-Idempotency-Production-Runbook.md`
（正常态/ Duplicate / RUNNING stale / IN_DOUBT 全流程 / Store unavailable /
Worker recovery / 人工 SQL 规范 / 保留策略，全部命令为本轮实测原样收录）

## 9. P0 / P1

```text
P0：无

P1-1（已修，本轮唯一生产代码修复）：CS 确认动作审计落库三层连环缺陷——
  ① AgentActionRecord.to_dict() 的 executed_at 为 ISO 字符串，asyncpg 拒绝
    TIMESTAMPTZ（DataError）；
  ② status='simulated' 违反 006 迁移 CHECK（应用契约 simulated|executed|failed
    与 DB 六值枚举交集仅 failed）；
  ③ 审计条目缺 conversation_id，agent_actions.conversation_id NOT NULL+FK 拒绝。
  生产后果：确认动作的 agent_actions/audit_logs 落库 100% 失败且仅 error 日志
  可见（agent_actions 全表 0 行、cs.action.persist 0 行实据），幂等执行本体
  不受影响但同事务审计设计（G13）实际失效。
  修复（最小三处）：
  - backend/customer_service/repository/audit_repo.py：executed_at str→datetime
    宽容转换（与 insert_audit_log 的 created_at 同款）；
  - backend/sql/migrations/050_agent_actions_status_enum.sql：status 枚举
    additive 扩入 simulated/executed（审计如实记「模拟执行」，不粉饰成 success），
    已登记 scripts/init_db.py MIGRATION_TARGETS 并实库 applied；
  - backend/customer_service/confirmation_flow.py：_handle_confirm 审计条目带
    conversation_id=session_id（confirmation 落库已幂等 get_or_create，FK 必然满足）。
  修复后实机复验：agent_actions 首行落库（1d01d498, simulated, fma-s1-g）、
  audit success 条目落库、双 ledger 闸门 succeeded、日志零失败。
  回归测试：test_audit.py 新增 4 用例（断言 ORM 字段类型，MagicMock 不吞类型错）；
  幂等冻结套件 26/26、契约门禁 36/36、customer_service 套件 1015+ 通过
  （2 失败经隔离归因均非本轮引入，见 §12）。
```

## 10. Deferred P2（登记，不扩 scope）

1. IN_DOUBT admin 列表页 / IN_DOUBT 与 stale RUNNING 告警规则（需新 gauge 指标）
2. resolve 未记录 operator 身份字段（以 reason 纪律 + 值班记录替代）
3. CS 卡片确认路径（POST /cs/confirm）不落 agent_actions/audit_logs（图路径已落；
   卡片路径效果仅存 ledger result）
4. 文本重复「确认」在 pending 清除后收到「系统繁忙」类兜底话术而非「已处理」
   （无副作用，纯 UX）
5. provider 幂等透传协议（business-service POST / Kafka event_id）——激活前必做
6. alerts webhook 重复通知可容忍（best-effort 观测面）
7. 审计条目 conversation_id 关联在 proposal 阶段仍为空（执行阶段已修）
8. PENDING+unacked / 管理面旁路等上游既有登记项维持不变

## 11. Changed Files（严格清单）

```text
M  backend/customer_service/repository/audit_repo.py        （P1 修复①）
M  backend/customer_service/confirmation_flow.py            （P1 修复③）
A  backend/sql/migrations/050_agent_actions_status_enum.sql （P1 修复②）
M  scripts/init_db.py                                        （050 登记 MIGRATION_TARGETS）
M  backend/tests/customer_service/test_audit.py             （回归 4 用例）
A  backend/scripts/idempotency_ops.py                       （运维 CLI：stale/status/resolve/probe）
A  docs/2026-09-24-Side-Effect-Idempotency-Production-Runbook.md
A  docs/2026-09-24-Side-Effect-Idempotency-人工生产验收-OPERATIONAL-FROZEN报告.md（本文件）
```

## 12. Tests / Manual Evidence

自动证据（本会话实跑）：
- `tests/customer_service/test_audit.py` 10/10（含新增 4 回归）
- `tests/test_side_effect_idempotency.py` 26/26（冻结套件，修复后复跑）
- 契约门禁 registry/layer/adr0001 36/36
- `tests/customer_service/ + test_side_effect_idempotency` 合跑 1015 passed +
  2 failed——归因隔离：`test_checkpoint::test_with_conversation_id`（期望无前缀
  thread_id，失败于并行会话**未提交**的 cs: 前缀工作树改动）；`TestDbUnavailable
  Claim::test_store_claim_falls_back_to_l1`（跨文件合跑 flaky，隔离复跑 15/15 过）。
  两者均与本轮三文件改动无关（所涉文件与 HEAD 零 diff）。

人工证据（真实网关 9080/真实 worker/真实 PG，全程记录于本报告 §3/§4）：
聊天确认全链 4 轮、并发双击、响应丢失重试、探针任务 4 键（fma-s4-a/fma-casea/
fma-caseb/fma-s6-fencing）、IN_DOUBT 三次保守阻断 + 三次人工裁决 + 重放复验、
owner CAS fencing。演练现场已复原（drill 容器删除、主 maintenance-worker 回归
主干 compose、fma 演练行保留作证据、stale=0）。

## 13. Git Closure

- 双重 pathspec 提交（`git add -A -- <paths>` + `git commit -- <paths>`）；
- 提交前逐文件核对：4 个修改文件此前均为 HEAD-clean（并行会话零混杂），
  引用符号（build_audit_entry conversation_id 参数、AuditRepository、
  celery_app/queue_router）全部已落库；
- 并行会话工作区（context_budget/travel/memory/pending_recovery 等 30+ 文件）
  零触碰、零收编；
- 运维窗口说明：app 容器重建 2 次（载入修复）、db-migrate 重建 1 次（050）、
  maintenance-worker 停机演练窗口约 12 分钟后回归主干配置。

## 14. Final Freeze

```text
MANUAL_SIDE_EFFECT_ACCEPTANCE_PASS=true
IDEMPOTENCY_OPERATIONAL_CLOSURE_PASS=true
SIDE_EFFECT_IDEMPOTENCY_OPERATIONAL_FROZEN=true
```

停止条件核对：人工 smoke ✓ / double click ✓ / retry+recovery 代表场景 ✓ /
IN_DOUBT 全流程 ✓ / provider inventory ✓ / 运维可发现三异常 ✓ / Runbook ✓ /
无 P0 ✓ / P1 唯一且已修复复验 ✓ / P2 全部明确 Deferred ✓。

本领域开发冻结：此后不重构 ledger / stable key / executor，不为测试更全追加
crash permutation，不为未来 provider 预建 adapter framework，不为管理 UI 推迟冻结。
```
