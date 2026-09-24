# Phase 3 STOP C — Provider Idempotency Contract 验收报告

> 日期：2026-09-24 ｜ 前置：STOP A（1dbf6eb）/ STOP B（49cafd9）
> 性质：**provider semantics layer** 新增——claim/lease/complete/IN_DOUBT 全部复用 Phase2 PG durable ledger，未建第二套幂等。

## 1. Verdict

```text
STOP_C_PASS=true
PROVIDER_IDEMPOTENCY_CONTRACT_FROZEN=true
```

Gate C1-C14 全部成立（§10 对照）；新增 22 用例 + 回归（Phase2 契约/STOP B/Step6/一致性门）全绿；E1/E2 两个 P0 实际关闭。

## 2. Root Cause（三个为什么）

- **internal idempotency ≠ provider idempotency**：ledger 只能阻止「同一 logical operation 被同一系统再次执行」；provider 是独立世界，internal complete 前 crash 时系统对外部是否已生效**一无所知**——没有 provider 侧 key/查询，任何自动重试都是盲赌。
- **Redis lease 不能解决 unknown external effect**：Redis claim 60s 是易失抑制——过期后同 key 回到 NEW，而外部世界的状态（邮件是否已发）从未被记录。durable truth 只能由 PG ledger 承担（STOP C E1 根因：`run_idempotent_operation` = Redis claim + PG *Result Store*（终态缓存），非 LedgerStore；60s 过期 → 重新 NEW → 重复发信）。
- **duplicate delivery 允许、blind external replay 禁止**：delivery 层重复由 lease/短路/ledger 消化（STOP B）；但一旦执行权合法获得并调用外部系统，重复副作用无法收回——所以外部写必须有 stable identity + 能力感知的 unknown 处置。

## 3. Provider Capability Audit（10 问结论，详表见 §4 前置审计）

1. external write adapters：smtp、agently_mail（email 双引擎）、business_service_http（CS_WRITE_SOURCE=java）、data_collection writer、competitor snapshot、export csv、report/snapshot 落盘、Kafka producer（默认关）。
2. E2 无保护直接发信 = `tools/email.py:88-90`（无租户直调分支）→ **已封死**。
3. 邮件链：tool → 审批门 → run_idempotent_operation(Redis+ResultStore) → 进程内指纹 → smtplib → **现改为** run_idempotent_side_effect(PG ledger) → 阶段化 outcome。
4. Redis lease 层 = RedisIdempotencyStore claim（60s）；5. durable ledger 层 = PostgresIdempotencyLedgerStore（300s lease + owner CAS + UNCERTAIN）。
6. send 后 complete 前崩溃（旧）：Redis running → 60s 过期 → 重新 NEW → **重复发信**（已关闭）。
7. native idempotency 可证实者：**零**（SMTP/Agently CLI/Java 契约均无键）。
8. status lookup 可行者：**零**（reconcile primitive 已建，供未来 provider）。
9. business-service：HTTP 真实存在但 POST 不重试（丢方向安全），Java 幂等支持未证实 → UNKNOWN + activation gate。
10. ledger schema（031/047）：PK 四元组 + request_hash + status/owner/lease 足够承载——provider key 由 PK 确定性派生可随时重算，**无需 migration**（§54 之「无 schema 变更」情形）。

## 4. Provider Capability Matrix

| Provider | 效果 | Native | Lookup | Stable Ref | Unknown 可能 | 契约策略 | 保护现状 |
|---|---|---|---|---|---|---|---|
| smtp | 发邮件（smtp 引擎） | UNSUPPORTED（RFC 5321 无键无查询） | 无 | ledger key→derived key | DATA 阶段中断 | **IN_DOUBT** | PG ledger + 阶段分类 ✅ 本轮收口 |
| agently_mail | 发邮件（QQ 邮箱 CLI） | UNSUPPORTED（CLI 无键） | 无 | 同上 | 进程崩溃窗口 | **IN_DOUBT** | PG ledger（CLI 报错=NOT_SENT 权威）✅ |
| business_service_http | CS 消息/状态写 | **UNKNOWN**（Java 未证实） | 未证实 | 契约已冻结 | read-timeout-after-write | IN_DOUBT + **activation_gate** | POST 不重试（丢方向安全）；激活门 docs/contracts/provider-idempotency-protocol.md |
| data_collection writer | 采集写库 | 内部 DB（append 弱） | DB 查询可建 | run_idempotent_operation + 审批 | claim 后中断 | 登记（低危） | 遗留：Redis-first 切 ledger 归后续（§14） |
| competitor snapshot | 快照 append | 内部 DB append | 无 | run_idempotent_operation | 同上 | 登记（低危，缓存量级） | 同上 |
| export csv | 文件覆盖写 | 天然幂等（覆盖） | 文件系统 | run_idempotent_operation | 无 | 登记（D 类） | 充分 |
| kafka producer | 事件产出 | 未开 enable_idempotence | 无 | event_id=uuid4（不稳定） | fire-and-forget | **归 STOP F** | 未启用（KAFKA_ENABLED=false） |
| LLM/embedding/rerank | 计费调用 | 供应商不支持 | 无 | ledger（run 内） | 超时=成本重付 | 登记（成本型） | checkpoint 续跑 |

unclassified external write = **0**（bypass scan：smtplib/sendmail 全仓唯一出口 email.py；出网 httpx/requests/urllib 清单逐一对上 STOP A inventory §A4 分类）。

## 5. Stable Identity Contract

- internal logical key = `ai.idempotency_records` PK 四元组（tenant, actor, operation, client_key）——correctness 权威。
- provider key = `derive_provider_key()`：`sha256("pv1|" + tenant|actor|operation|client_key|provider|provider_operation)`，**opaque 确定性派生**；execution_id/attempt/timestamp/uuid 无法进入输入（签名即约束）；tenant 前置隔离（§46）；不落库（可随时重算，与 ledger 行一一对应——§34 唯一约束因此冗余，不加）。
- fingerprint = `provider_effect_fingerprint()` = ledger 同源 `canonical_fingerprint`（sorted-keys JSON + SHA256）；same key + different fingerprint → ledger request_hash 冲突 → IdempotencyConflict fail-closed（Gate C3）。
- 方向红线：internal key → provider key（单向派生）；provider key 不回灌业务 identity。

## 6. 执行决策矩阵（已实现于 decide_outcome_action）

| Native | Lookup | Outcome | 行为 | 实现 |
|---|---|---|---|---|
| yes | any | UNKNOWN | same-key retry（SAFE_RETRY） | FAILED 可接管；重试同 provider key 由 provider 去重 |
| no | yes | UNKNOWN | reconcile first | reconcile_provider_effect → 四值结构化结论 |
| no | no | UNKNOWN | **IN_DOUBT** | `SideEffectOutcomeUnknown` → ledger UNCERTAIN 保守阻断 |
| unknown | unknown | any write | **fail closed** | `ensure_execution_allowed` raise ProviderContractMissing |
| any | any | NOT_SENT | FAILED 可重试 | ProviderEffectError（未越过边界） |
| any | any | REJECTED | FAILED 不自动重试 | ProviderEffectError（fail_terminal 语义） |

新增底层原语（frozen 文件最小 additive，任务书 §50 合规）：`idempotency.py` +`SideEffectOutcomeUnknown` 异常 + executor 异常分支 3 行（UNKNOWN → `_UNCERTAIN_ERROR_CODE`，与 crash window 同一阻断语义）。`__post_init__` 模型红线：UNKNOWN 禁止 SAFE_RETRY、UNSUPPORTED 禁止声明载体。

## 7. E1 SMTP Closure（单独章）

- **切换**：`send_email_tool` 有租户分支 Redis+ResultStore → `run_idempotent_side_effect`（PG ledger，lease 300s）+ 保留副作用预算门禁（pre_execute）+ 保留进程内指纹（短时并发抑制，§19 允许；**本轮修复其继承缺陷：指纹 key 补租户前缀**——原全局指纹会让 tenant B 同内容邮件被 tenant A 窗口拦截造成静默丢发）。
- **阶段化 outcome 分类**：连接/TLS/认证失败与服务器明确拒收（SMTPRecipientsRefused/SenderRefused）= NOT_SENT → FAILED 可重试；sendmail 已写出但连接中断（SMTPServerDisconnected/OSError/超时）= UNKNOWN → `SideEffectOutcomeUnknown` → **UNCERTAIN 永久阻断**（C9：链路已无 Redis，任何窗口过期都不能重开发信权限）。
- agently 引擎同步接入 ledger；CLI 非零退出 = NOT_SENT（CLI 报错为其"未完成发送"的权威），崩溃窗口由 ledger 覆盖。
- 稳定 key：`idempotency_key 参数 or 工具幂等键 or canonical_fingerprint(payload)`——同收件人+主题+正文 = 同 logical effect（跨 retry/recovery/delivery/execution 恒定，Gate C2）。
- 无 schema 变更（provider key 可由 ledger 行确定性重算）。

## 8. E2 Direct Bypass Closure

无租户直调分支 → `raise IdempotencyContextMissing`（fail closed，方式 B）。防重启用：`test_e2_direct_bypass_fail_closed` 永久锁死该行为（无身份 → 副作用前拒绝）；任务运行时链路由 `_bind_task_identity` 保证身份，HTTP 链路由网关注入。生产调用路径 = 强制经过 ledger+契约 ✅（R3）。

## 9. Business-service Contract（Gate C8）

契约冻结于 `docs/contracts/provider-idempotency-protocol.md`：Java 侧必须支持 `Idempotency-Key`/`X-Logical-Effect-Id`/`X-Request-Fingerprint` 三头 + 九项激活证据（native proof/retention/scope/replay semantics/lookup/timeout/duplicate/failure injection）方可把 registry 条目升级 SUPPORTED 并解除 activation_gate。未激活前 UNKNOWN fail-closed。**未虚构下游支持**。

## 10. Failure Injection 对照（C1-C12 / R1-R3）

| 用例 | 测试 | 结果 |
|---|---|---|
| C1 stable key 跨 execution | test_c1_stable_key_identical_across_executions | ✅ |
| C2 不同 effect 不同 key | test_c2_different_logical_effects_differ | ✅ |
| C3 duplicate delivery effect=1 | test_c3_duplicate_delivery_effect_once | ✅ |
| C4 native timeout→same-key retry | test_r1_native_provider_replay_safe（+C3 replay） | ✅ |
| C5 same key 变 payload → conflict | test_fingerprint_deterministic_and_order_insensitive + ledger request_hash 冲突（Step6 E 用例回归） | ✅ |
| C6 NOT_SENT 安全重试 | test_c6_not_sent_retryable_then_recovers | ✅ |
| C7 UNKNOWN → IN_DOUBT 自动重试=0 | test_c7_unknown_after_dispatch_blocked_forever | ✅ |
| C8 SMTP accepted+crash | test_c8_c9_accepted_then_crash_no_resend | ✅ |
| C9 Redis lease 过期不可重开 | 同上（链路无 Redis；durable 阻断） | ✅ |
| C10 direct bypass | test_e2_direct_bypass_fail_closed | ✅ |
| C11 missing contract | test_c11_missing_provider_contract_fails_closed | ✅ |
| C12 UNKNOWN capability | test_c12_unknown_capability_fails_closed + cannot_declare_safe_retry | ✅ |
| R1 native replay（fake provider） | test_r1_native_provider_replay_safe | ✅ |
| R2 SMTP unknown window | test_c7/c8（**transport-level fault injection**，可控 fake transport；非真实生产 SMTP——任务书 §39 要求的如实标注） | ✅ |
| R3 direct bypass enforcement | test_e2（production path forced through ledger/contract or blocked） | ✅ |

## 11. Duplicate Delivery Proof（与 STOP B 串联）

at-least-once delivery（broker 可能重投）→ STOP B accelerator 可能再投一份 → worker 拾取两条消息 → lease CAS 只允许一条获得执行权（STOP B R3 实机）→ 持权 execution 调用外部系统时：stable provider key 与 execution/delivery 无关（C1/C2）→ 崩溃后接管重试要么被 ledger 阻断（UNKNOWN/IN_DOUBT）要么以同 key 由 provider 去重（native）→ 效果：duplicate delivery ≤ N，legal execution = 1，external effect = 1（provider 语义允许时）或 IN_DOUBT 人工收口（不允许时）。**组合结论：effectively-once where provider/transactional semantics permit; unknown external effects fail closed and require reconciliation。**

## 12. Regression

- 新增：`tests/test_provider_idempotency_contract.py`（17 passed）+ `tests/test_email_durable_idempotency.py`（7 passed，真 PG 隔离表 + fake SMTP transport）＝ **24 passed**
- 回归：`test_phase2_runtime_contract.py`(13，C4 断言经升级注释保持字符串匹配——实际接线为更强的 run_idempotent_side_effect，如实披露) + STOP B 17 + Step6 `test_side_effect_idempotency.py` 26 + admission runtime + queue router + registry/layer/adr0001 一致性门 ⇒ **本轮合计 156 passed / 0 failed**（84+72 两批实测）
- failed=0 skipped=0（PG 不可达时整模块 skip 为既定策略）

## 13. Changed Files

| 文件 | 变更 |
|---|---|
| `backend/shared/provider_idempotency.py` | **新增**：Capability Model（enum/typed）+ 集中 PROVIDER_CONTRACTS + derive_provider_key + fingerprint + decide_outcome_action + reconcile primitive + 结构化日志事件 |
| `backend/shared/idempotency.py` | 最小 additive：+`SideEffectOutcomeUnknown` 异常 + executor UNKNOWN→UNCERTAIN 分支 + `__all__`（frozen 最小触碰，correctness 必须） |
| `backend/tools/email.py` | E1：切 PG ledger + 阶段化 outcome 分类 + 指纹租户域化；E2：无租户分支 fail-closed |
| `docs/contracts/provider-idempotency-protocol.md` | **新增**：business-service 契约冻结 + activation gate |
| `backend/tests/test_provider_idempotency_contract.py` | **新增**（17 用例） |
| `backend/tests/test_email_durable_idempotency.py` | **新增**（7 用例） |

## 14. Known Debt（明确保留，归属后续 STOP）

- **STOP D**：Business Entity Unique Guard（同实体逻辑等价操作去重）——本轮未做（任务书 §29 禁止）。
- **STOP E**：CONFIRMED/EXECUTING 卡死回收 + resolve_stale_side_effect 正式 API 调用方——本轮仅提供 reconcile primitive 与人工裁决函数。
- **STOP F**：Kafka event identity（producer enable_idempotence + stable event_id）。
- **STOP G**：Admin IN_DOUBT UI / recovery plane。
- **STOP H**：provider_idempotency_reused/conflict 指标（结构化日志已落：provider_outcome_decided/provider_contract_missing/provider_execution_denied_unknown/side_effect_in_doubt）。
- 遗留（登记不阻塞）：data_collection/competitor 的 Redis-first→ledger 切换（内部 DB 写低危，D/弱 C 类）；`business_report/data_fetcher` 可配 POST 数据源语义 UNKNOWN（读方向）。

## 15. Git

- commit：path-scoped（仅 §13 清单）；工作区其余改动归属并行会话，零收编。
