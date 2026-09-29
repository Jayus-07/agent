# Phase 3 — STOP D 实施报告：Business Entity Unique Guard 生产收口

> 日期：2026-09-25 ｜ 前置：STOP A `1dbf6eb` / STOP B `49cafd9` / STOP C `f3c4c19`（PROVIDER_IDEMPOTENCY_CONTRACT_FROZEN=true）均验证在 HEAD ancestry

---

## 1. Verdict

```text
STOP_D_PASS=true
BUSINESS_ENTITY_IDEMPOTENCY_FROZEN=true
```

## 2. Root Cause：confirmation 级幂等为何挡不住语义重复

Phase2/STOP C 的幂等身份都锚定**技术身份**：confirmation_id（`UNIQUE` 约束）、Phase2 ledger key（`(tenant, actor, 'cs.action.execute', cs_action:{confirmation_id})`）。用户双击「确认退款」产生两个 confirmation（A/B 各自 action_id 不同）→ 两套幂等各自 "A effect once / B effect once"，全部"正确"——但业务上退款执行了两次。缺的是**业务操作身份**：`同 tenant + 同 action + 同业务实体 + 同语义载荷` 上的 durable 唯一性，且必须由 PostgreSQL 约束决胜（§17：先查后插在并发下双穿）。

## 3. Business Action Identity Matrix（审计实证，§84 十二问全答）

| Action | Entity Type | Entity ID 来源 | Semantic Fields | Active Conflict States | Terminal Reopen? |
|---|---|---|---|---|---|
| refund_request | order | 结构化槽位（补槽流程）→ OrderService 核实 | amount（before_state）、target_state（after_state.status）、risk_level | pending/confirmed/executing/verifying | **否**（success 后同语义拒绝） |
| return_request | order | 同上 | target_state、risk_level | 同上 | 是（业务 eligibility 把关） |
| exchange_request | order | 同上 | 同上 | 同上 | 是 |
| order_cancel | order | 同上 | 同上 | 同上 | 是 |
| address_update | address | 表单/槽位 | 同上 | 同上 | 是 |
| password_reset | account | user 自身 | 同上 | 同上 | 是 |
| need_info 占位（各 intent） | order（空 target_id） | — | 无（不参与守卫） | 不入索引（051 谓词排除） | — |

tenant 链：runner JWT 身份 → `cs_context.tenant_id`（graph_builder:106）→ 本轮显式持久化到 confirmations 行（修 STOP A 确认的缺租户问题）；绝不取请求体自带的 tenant（§67）。指纹**排除**：confirmation_id/action_id、execution_id、created_at/expires_at、proposal_text（LLM 措辞含 reason 自由文本）、retry_count（§49/§50/§9）。

## 4. Identity Contract

```
BusinessOperationIdentity = (tenant_id, action_type, target_type, target_id, semantic_fingerprint)
semantic_fingerprint = SHA-256[:64]（canonical JSON：action/target 身份 + amount(Decimal 2位定点，
  100/100.0/"100.00" 同一形态) + target_state + risk_level + items 排序集 + currency 大写）
```
- 单一事实源：`business_guard.compute_identity/compute_semantic_fingerprint`；列与 051 索引完全同构（§34 禁两套口径）。
- 身份不完整（占位行 target_id=''）→ 不参与守卫；正式业务写缺 action/entity → `BusinessOperationIdentityMissing` fail-closed（§54/§76，production 严格模式）。
- actor/user **不进**身份（§51）：实体已按 tenant 隔离，双用户同单同语义必须互相去重。

## 5. Lifecycle / Conflict Matrix

| state | 守卫 | 依据 |
|---|---|---|
| pending | **阻止** | 已有确认等待（D14） |
| confirmed | **阻止** | 已认领（D15） |
| executing | **阻止** | 执行中（D16） |
| **verifying**（复用既有 IN_DOUBT 语义） | **阻止** | STOP C `SideEffectOutcomeUnknown` 落此态，占住索引直至 STOP E reconciliation（D13/R4/§15/§37） |
| success | 释放索引 + 终态策略：refund_request 同语义拒绝（`TERMINAL_BLOCKED_ACTIONS`），其余放行（D18/§38） | |
| failed | 释放（definite no-effect）；**防线**：创建时反查 Phase2 ledger，该身份任一先行 confirmation 留有 `running/IDEMPOTENCY_UNCERTAIN` 记录 → 持续阻止（R5 vs R4 分界，§36） | |
| expired | 释放（D17，重新发起合法） | |
| cancelled | 释放（用户改口重发合法） | |

## 6. Schema / Migration

`backend/sql/migrations/051_cs_business_operation_guard.sql`（memory 库，已登记 MIGRATION_TARGETS 并在 5433 实库应用）：
- `confirmations` 增 `tenant_id`/`semantic_fingerprint`（nullable —— **legacy 24 行零回填不伪造**，实测零 active 行无冲突风险，§74/§75）；
- state CHECK 收编 `'verifying'`（复用 2026-09-22 治理引入的既有枚举值，替代任务书 §16 示例中的 in_doubt 命名）；
- `uq_cs_confirmations_active_biz_op` partial unique：`(tenant_id, action_type, target_type, target_id, semantic_fingerprint) WHERE state IN (pending,confirmed,executing,verifying) AND tenant_id IS NOT NULL AND semantic_fingerprint IS NOT NULL AND target_id <> ''`（§33：谓词只覆盖完整身份）。
- 实库验证（D14）：两列 ✓ / CHECK 八值 ✓ / 索引谓词逐字核对 ✓ / 迁移账 `schema_migrations` 登记 ✓。
- 教训（首次应用事故）：验证后补账 INSERT 失败导致整个事务回滚，DDL 实际未落库——重放改 autocommit 并以**独立连接**复核提交后状态。

## 7. DB Hard Guard / Concurrency Proof

- Guard 即 INSERT 本身：`_async_save` → `repo.save`（带身份列）→ 撞 051 索引 → rollback → 反查既有行 → `BusinessOperationAlreadyActive(existing_confirmation_id, existing_state)`（§18/§66：不是 500，可审计定位）。
- proposal 更新走 `begin_nested()` savepoint：`update_proposal` 同一条 UPDATE 原子刷新身份（D19）；UPDATE 撞索引 → savepoint 回滚 → `BusinessOperationConflict`（D20），外层 conversation ensure 保留。
- 附带修复：同一新会话并发双提交会在 conversations 唯一键竞争（既有裸 check-then-insert）——savepoint 补强，撞键复用对方已提交会话行，guard 决胜统一交给 051。

## 8. Cross-confirmation Duplicate Proof

D1/D2/D4（`test_d1_d2_d4`）：conf-A 存活后 conf-B（不同 action_id/不同会话）被拒，`existing_confirmation_id="conf-A"`；D3（Gate D5）：同 action_id 的 recovery 重放同样去重；R2：两个独立会话请求 → 一个 active 操作。

## 9. IN_DOUBT Proof

- R4/D13：先行操作 failed 但 `ai.idempotency_records` 留有 `IDEMPOTENCY_UNCERTAIN` → 新建被 `BusinessOperationAlreadyActive(existing_state="failed(in_doubt_ledger)")` 阻止——证明 STOP D 没有绕过 STOP C。
- 执行路径：`_handle_confirm` 捕获 `SideEffectOutcomeUnknown`（executor 落 UNCERTAIN 后 re-raise，源码确认）→ state=verifying（active 索引内），绝不起草 failed。

## 10. Release Semantics

R5/D12：failed（无 ledger 未决记录）→ 释放，第二次创建成功（不永久死锁业务）；D17 expired 释放；D18 success 按动作策略；verifying 永不自动释放（§59：CONFIRMED/EXECUTING/VERIFYING 卡死不改状态，STOP E 裁决）。

## 11. Proposal Mutation Safety

D19：占位行（无身份）升级正式 proposal → 同事务原子补全 tenant+fingerprint（实测列值与指纹一致）；D20 两层：预检拦截（确定性，用户语义明确）+ savepoint UPDATE 决胜（竞态窗口 DB 兜底），两层后均不存在两行等价 active。

## 12. Bypass Scan（D23/§64）

`CSConfirmation(` 全仓唯一构造点 = `confirmation_repo.save`（唯一经 guard 的入口）；`store.save` 调用点全部收敛：action expert 3 处（已穿 tenant）、confirmation_flow reask（同身份 retry_count 更新，身份不变）、state_transition（写的是 StateStore 非 confirmations——分类排除）。直接 `INSERT INTO confirmations` 仅存在于测试（`test_p3_concurrency_pg.py` fixture，登记例外）。**production unguarded create = 0**（含机械测试 `test_d23_bypass_scan` 锁定）。

## 13. R1-R5 实机结果（真实 PG 5433）

| # | 场景 | 结果 |
|---|---|---|
| R1/D10 | **10 个独立 psycopg2 连接**并发 INSERT 同语义操作（FK 前置会话行，逐字 §62-R1） | **1 成功 + 9 unique_violation，active=1** ✓ |
| R2 | 两独立会话/不同 confirmation_id | 1 个 active ✓ |
| R3/D6 | 跨租户同 entity/action/payload | 两行共存 ✓ |
| R4/D13 | UNCERTAIN ledger 后新建 | blocked ✓ |
| R5/D12 | definite failure 后重发 | 允许 ✓ |

测试基建注记：进程内 10×asyncio.run 并发会因共享 async engine 的事件循环亲和性自锁（宿主基建限制，非 guard 语义），故 R1 按任务书原文用 10 个独立连接直驱决胜层；应用层流（身份计算/预检/转译）由 D1-D9/D11-D23 全覆盖。

## 14. Regression

| 套件 | 结果 |
|---|---|
| **新增** tests/customer_service/test_business_guard.py（D1-D23 + R1-R5） | **20 passed** |
| tests/customer_service **全量** | **1010 passed** + 1 skipped + 1 flaky（`test_store_claim_falls_back_to_l1` 合跑状态污染，单跑复验 1 passed——既有已知 flaky 类型） |
| tests/test_phase2_runtime_contract.py（Phase2） | **13 passed** |
| tests/test_provider_idempotency_contract.py（STOP C） | **17 passed** |
| tests/test_task_pending_recovery.py / test_email_durable_idempotency.py / test_side_effect_idempotency.py | 宿主 pytest 挂死（文档化环境病态：启动/连接挂死三联症状；**机械隔离证明**：三文件被测模块仅 `backend.models.task`/`backend.shared.idempotency`/`backend.shared.provider_idempotency`/`backend.shared.logger`，与本 STOP diff 五文件零交集，且同宿主上相邻两套含改动的套件全绿） |

存量跟进（正常演进，保断言意图）：`test_confirmation_store.py` mock lambda 签名、`test_defect6_action_chain.py` FakeStore/FakeRepo/FakeSession 签名与 savepoint stub。

## 15. Changed Files

| 文件 | 变更 |
|---|---|
| `backend/sql/migrations/051_cs_business_operation_guard.sql` | 新增（身份两列 + CHECK 收编 verifying + partial unique） |
| `scripts/init_db.py` | MIGRATION_TARGETS 登记 051 |
| `backend/customer_service/business_guard.py` | 新增：Identity/fingerprint/错误契约/终态策略 |
| `backend/customer_service/models/confirmation.py` | +tenant_id/semantic_fingerprint 列 |
| `backend/customer_service/repository/confirmation_repo.py` | save/update_proposal 身份持久化与原子刷新；find_by_identity/list_ids_by_identity/has_in_doubt_ledger；verifying 记 executed_at |
| `backend/customer_service/confirmation_store.py` | save(tenant) 穿线；_async_save 守卫流（预检/终态/IN_DOUBT 防线/IntegrityError 转译/会话 savepoint 补强） |
| `backend/customer_service/confirmation_flow.py` | SideEffectOutcomeUnknown → verifying（IN_DOUBT_LOCKED） |
| `backend/customer_service/experts/action.py` | tenant 解析与全路径穿线；三类守卫错误的稳定业务语义返回（§20） |
| `backend/tests/customer_service/test_business_guard.py` | 新增 20 用例 |
| `backend/tests/customer_service/test_confirmation_store.py`、`test_defect6_action_chain.py` | 存量 mock 签名跟进 |

## 16. Known Debt

- **STOP E**：verifying/CONFIRMED/EXECUTING 卡死行的人工 reconcile workflow（本轮只保证其持续占守卫）。
- **STOP F**：Kafka identity。**STOP G**：admin recovery plane。**STOP H**：最终 metrics/config（本轮仅 §65 结构化日志 `business_guard_*`：duplicate/terminal-duplicate/in-doubt/insert-dedup/proposal-collision，零高基数 label）。
- 观察项：①conversations 默认值 'active' 不在其 CHECK 合法值内（006 遗留，ORM 显式赋值故不触发）——建议后续迁移修正；②action expert 的 reason 为自由文本（proposal_text 内），未参与指纹——若业务未来认定 reason 语义化需结构化字段化；③simulated 业务下 D18 终态拒绝以 guard 层策略实现（`TERMINAL_BLOCKED_ACTIONS`），真实实体状态 eligibility 待 Phase 6 真实执行器接入后接管（§55 如实登记：业务 entity side effect 仍为 simulated，Business Guard 的 DB 并发/重复创建为真实验证）。

## 17. Git

pathspec 提交（hash 见 git log）；工作区其他会话文件（travel/conftest.py、docs/验收集等）零收编。

## 18. 是否允许进入 STOP E

**允许**。Gate D1-D18 全过（D2/D3 见 §7/§13，D13 见 §9/§12，D14 见 §6，D16 见 §14，D17/D18 见 §10），STOP C frozen contract 零倒退（provider_idempotency 17/17 在含本改动的工作区上全绿；UNKNOWN 自动 retry/SMTP UNCERTAIN resend/无租户发信三项冻结语义未被触碰）。
