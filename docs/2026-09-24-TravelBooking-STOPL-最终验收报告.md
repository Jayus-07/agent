# Travel Booking Transaction & Order Lifecycle — STOP L 最终验收报告

> 日期：2026-09-25（开工 09-24）｜ 基线：STOP K 冻结（`7f0d0b2`）
> 审计与设计：`docs/2026-09-24-TravelBooking-STOPL0-审计与设计.md`
> 提交链：L0 `2b856b2` → L1-L9 `f6566c6` → L8 评测 `5eab121` → P-07 修复
> `c322ac3` → 本报告

---

## 1. Verdict

```text
STOP_L_PASS=false
BLOCKED_BY_EXTERNAL_BOOKING_PROVIDER=true

TRAVEL_BOOKING_TRANSACTION_CORE_PASS=true
TRAVEL_BOOKING_TRANSACTION_CORE_FROZEN=true

REAL_BOOKING_PROVIDER_E2E_PASS=false
```

判定依据（§五十六）：G1-G44 全过（G41 证据=网关 E2E §31），但仓库不存在
任何支持 Booking Create 的真实外部 Provider——G45/G46（Real Booking
Provider E2E / replay-query-reconciliation E2E）如实 BLOCKED。核心架构
（状态机/幂等语义/确认语义/Quote 契约/Webhook Inbox/Reconciliation/
Recovery/审计）冻结；**绝不以 Fake Provider 把 STOP_L_PASS 刷成 true**
（§二）；STOP K 的 `STOP_K_PASS=false` 保持不变（§四十九）。

## 2. Frozen Baseline

STOP I（行程质量）/ J（Provider 层）/ K（Commerce 契约，K 保持
BLOCKED）+ Phase2 异步运行时 + Step6 副作用幂等 + Phase3 STOP C Provider
幂等契约。开工基线：`7f0d0b2` 工作区（并行会话仅 travel conftest + 两个
docs 未提交改动，全程未触碰）。

## 3. STOP L0 Audit（结论）

全仓审计（关键词 booking/order/transaction/confirmation/pending/idempotency/
side_effect/outbox/webhook/inbox/reconciliation/recovery/lease/execution_id/
merchant_order/external_reference/provider_reference）：

| 审计项 | 结论 | 复用方式 |
|--------|------|---------|
| 幂等设施 | **已有**：`shared/idempotency.py` PG 权威账本（claim/lease/owner CAS/接管/UNCERTAIN 阻断/人工裁决） | Booking executor 直接装配（G2 ✓，无第二套账本） |
| Provider 幂等契约 | **已有**：`shared/provider_idempotency.py`（集中 registry + 三模型决策矩阵 + derive_provider_key + 结构化 reconcile） | 三条 fake booking 契约登记（加法式） |
| Confirmation | CS 域内单一实现（session 键 + CS 错误类型），非跨域设施 | 用户确认实现为 BookingOrder 状态机内嵌 gate（AWAITING_CONFIRMATION→CONFIRMED 携带绑定指纹 + DB 条件更新原子认领，复用 CS claim 模式）——无第二套确认状态机（G3 ✓） |
| Task/Recovery | **已有**：Celery 五队列 + QueueRouter 显式登记 + maintenance beat 任务族 | `travel.booking_recovery_scan` 登记 maintenance 队列（加法式） |
| Outbox | CS 域有完整 PG transactional outbox（customer_service.events + 同事务 append + SKIP LOCKED relay），schema 绑定 CS | booking_events 采用**同一模式**（同事务 append + event_id 唯一 + relay-ready），域内建账（G4 ✓，模式复用而非复刻系统） |
| Webhook Inbox | **无** | 本轮新建（§18） |
| merchant_order/external_reference | 全仓零命中 | 本轮全新建设（§11） |
| business_guard 四层身份 | migration 051 前例 | 身份分层沿用（§20） |

## 4. Before Architecture

全仓零订单状态机；`providers/travel/booking.py` 为 Phase 7 纯预留
（Protocol + 状态机 + 幂等键，零接线）；commerce 止步 Deep Link；「订」类
交易意图无任何承接链路。

## 5. Booking Architecture

（L0 §8 定稿，实现零偏离）

```text
用户（帮我预订大阪10月3日到5日的酒店 → 确认预订 → 我的预订）
    ↓ APISIX :9080（JWT）
router_node：客服 > 旅游 > 选品 > 预订（新增）> 商务 > 主 Router
    ↓ route_mode="travel_booking"
travel-booking 域图（2 节点线性，零 LLM，无 checkpointer；订单事实全在 PG）
    booking_resolver（新预订/确认/状态三子意图）→ booking_executor
    ↓
travel/booking/：service 编排 →（确定性重搜选 offer）→ Quote 落库
    → 确认 gate（绑定指纹 + 原子认领）→ 价格/库存复核（P0）
    → executor（幂等账本 claim → Provider 调用 → 决策矩阵 → 状态 CAS）
    ↓
providers/travel/booking/（原预留 booking.py 升格为包；contracts + 三能力
    profile fake）→ shared/idempotency + shared/provider_idempotency（冻结）
```

**禁止事项落地**：无 Travel Graph 直连 provider.book()（§七）；外部调用
不在长 DB 事务中（prepare CAS → 外呼 → finalize CAS，§二十二）；无第二套
幂等/确认/Outbox 系统。

## 6. Scope In / Out

In（§三全项）：Quote/Intent/Explicit Confirmation/Revalidation/Booking
Order/Create/Provider 幂等能力/Internal Idempotency/External Reference/
Webhook Inbox/状态机/IN_DOUBT/Reconciliation/Recovery/Outbox-Audit/授权
租户/可观测/评测/网关 E2E。

Out（§四原文）：Raw Card/Payment Capture/Refund/Change/Exchange/
Chargeback/Cancel Policy/Loyalty/Invoice/FX——留 STOP M。
payment_deep_link 仅 Provider 返回时经 deeplink 校验保存展示；本系统不做
PCI processor。PII：Fake 流程零 PII 字段（§三十四：不为未来猜字段）。

## 7. Provider Capability Matrix

| provider | native key | client ref | lookup | webhook | unknown policy | activation |
|----------|-----------|-----------|--------|---------|----------------|-----------|
| fake_booking_native（A） | SUPPORTED（body） | ✓ | ✓ | ✓（inbox 注入） | SAFE_RETRY | 测试/评测 |
| fake_booking_clientref（B） | UNSUPPORTED | ✓ | ✓ | ✗ | RECONCILE_FIRST | 测试/评测 |
| fake_booking_bare（C） | ✗ | ✗ | ✗ | ✗ | IN_DOUBT | 测试/评测 |
| 真实供应商 | UNKNOWN | — | — | — | fail-closed（拒绝执行） | 无凭据 |

能力声明在集中 registry（`PROVIDER_CONTRACTS`，唯一事实源，附证据）；
UNKNOWN 一律 fail-closed（§五.E）。`TRAVEL_BOOKING_PROVIDER` 选型；
配置真实供应商名 = ProviderContractMissing（不静默降级 fake）。

## 8. Quote Contract

不可变事实快照（非 Offer 指针）：quote_id/tenant/user/commerce_type/
provider/provider_offer_id/offer_fingerprint/booking_facts/price_amount
(NUMERIC 18,4)/currency/taxes(None=unknown)/fees/tax_inclusion/availability/
provider_observed_at/quote_created_at/provider_expires_at(仅 Provider 明示)/
internal_expires_at(系统确认 TTL)/quote_fingerprint/status。
**DB 级不可变守卫**：BEFORE UPDATE trigger 拒改价格/币种/指纹列（应用层
失守也改不动）。价格变化 = 旧 Quote superseded + 新 Quote + 重新确认。

## 9. Confirmation Contract

确认绑定快照指纹 = sha256(quote_id|quote_fingerprint|amount|currency|
provider|operation|tenant|user)（§十一：确认具体价格与具体订单事实，
不是 confirmed=true）。确认时重算指纹与 gate 绑定一致才放行；TTL=
quote.internal_expires_at；过期 → EXPIRED（create=0）。
**确认后价格变化（P0，G9）**：复核不一致 → 停止执行 + 旧确认失效 +
旧 Quote superseded + 新 Quote + 要求重新确认（create=0，Decimal 精确
比较）；订单与 Quote 金额服务端互检（G32）。库存售罄/offer 消失/复核
不可用 → 全部 create=0 + 如实终止（G10；禁止假定 AVAILABLE）。

## 10. Price/Availability Revalidation

复核 = 复用 commerce service **确定性重搜**（同参数同排序，缓存吸收）+
offer_fingerprint 精确匹配。四种结局：ok / price_changed（新价入替代
Quote）/ offer_gone / sold_out / unavailable——后四种 create 一律 = 0。

## 11. Booking Intent

`booking_intent_id = uuid5(tenant|user|quote_id|confirmation_fingerprint)`；
`merchant_order_id = "MOB-" + sha256(tenant|intent|provider)[:20]`；
`idempotency_key = derive_provider_key(...)`（opaque，native 模式作为
Idempotency-Key 传递）。双击/刷新/SSE 重连/HTTP retry/worker retry 派生
同一 intent（G11/G12，确定性测试钉死）。

## 12. Idempotency

复用全局 PG 账本 `ai.idempotency_records`：operation=travel.booking.create，
client_key=merchant_order_id，owner_execution_id 不进 key；接管策略 =
Model A only（SAFE_RETRY）；B/C 的 stale claim 保守 UNCERTAIN 阻断，
由 Reconciliation/人工解除。DB 唯一约束兜底并发：orders 表
booking_intent_id/merchant_order_id/idempotency_key 各 UNIQUE（G13）。

## 13. Order State Machine

QUOTED → AWAITING_CONFIRMATION → CONFIRMED → SUBMITTING → BOOKED；
CONFIRMED→FAILED（价格/库存阻断，create=0）；SUBMITTING→FAILED
（NOT_SENT 可重入 / REJECTED 终态）；SUBMITTING→IN_DOUBT（结果未知）；
IN_DOUBT→BOOKED/FAILED **仅 cause=reconciliation|manual**。
集中转换：白名单 + status_version 单调 + 同事务事件追加；非法转换
fail-closed（G20/G21/G22）。不加 RECONCILING（对账是动作不是状态）。

## 14. Crash Window Analysis

| 窗口 | 恢复 | 证据 |
|------|------|------|
| W1 claim 后 provider 前 crash | A：lease 过期 + takeover_allowed(SAFE_RETRY) 同 key 重放；B：恢复扫描先 lookup；C：保持 IN_DOUBT | B7/§四十五 |
| W2 已发出本地 timeout | 决策矩阵按能力分派 | B6/B9/B10 |
| W3 provider 成功本地未写 BOOKED | A 重放返回既有订单 / B lookup→BOOKED / C 人工 | B7/B9（create=1 断言） |
| W4 BOOKED 已落库响应未达 | 状态查询幂等收口 | B4/E2E-3 |
| W5 webhook 早于 HTTP 收尾 | inbox 先落库；SUBMITTING→BOOKED 合法转换；HTTP 收尾 CAS 不命中幂等 | B16 |
| W6 webhook 重复 | (provider,event_id) 唯一 + 应用幂等 | B16 |
| W7 webhook 乱序 | 状态版本单调 + 终态守卫（迟到 pending 不倒退 BOOKED） | B17 |

## 15. IN_DOUBT Semantics

进入：决策矩阵 in_doubt / 接管被拒 / 终态回写失败（UNCERTAIN）。
表现：渲染「供应商端结果暂时无法确认，系统不会自动重复提交该预订，正在
等待状态核实」——禁止成功/失败话术（G37 口径，测试钉死）。
出口：仅 reconciliation 证明 / 人工裁决。账本侧 UNCERTAIN 阻断同 key
自动重试（共享层冻结语义）。

## 16. Reconciliation（G30：查询事实 ≠ 重试）

`reconcile_order`：lookup（按 merchant ref，能力账决定）→
KNOWN_SUCCESS→BOOKED / KNOWN_FAILURE→FAILED /
NOT_FOUND_SAFE_TO_RETRY→`resolve_stale_side_effect(not_executed)` 解除
阻断后允许重入（订单回 FAILED(not_sent)，重新走全部确认门）/
无法判断→保持 IN_DOUBT。触发：recovery beat 扫描 + 人工接口。

## 17. Recovery

`travel.booking_recovery_scan`（beat 60s，maintenance 队列，显式路由登记）：
① awaiting_confirmation 且 TTL 到 → EXPIRED（兜底，不要求用户重点）；
② stale SUBMITTING：Model A 重入 executor（同 key 重放）；B/C → 对账；
③ IN_DOUBT → 对账/保持。全部动作经状态机白名单 + 幂等账本，恢复自身
幂等（重复投递无害）。测试：B20/§四十五。

## 18. Webhook Inbox

`travel.booking_webhook_inbox`（(provider,event_id) UNIQUE）。入站校验：
HMAC-SHA256(secret, timestamp.payload) + ±300s 重放窗口 + provider
白名单 + payload ≤64KB + content-type 白名单；secret 仅 env，零日志/
trace/响应。应用：merchant ref 找单（找不到 → quarantine，绝不 attach
最近订单）；SUBMITTING→BOOKED/FAILED 合法；终态迟到事件幂等忽略。
B16-B19 全过。

## 19. Outbox / Audit

`travel.booking_events`（append-only，event_id UNIQUE）：与状态转换
**同一事务**追加（状态+事件原子提交，无丢事件窗口）。事件覆盖 §四十一
全清单：quote_created / booking_intent_created / confirmation_confirmed /
booking_submit_started / booking_succeeded / booking_failed /
booking_in_doubt / booking_confirmation_expired / booking_blocked_* /
reconciliation_resolved / webhook_applied（T11=1.0）。

## 20. Authorization / Tenant Isolation

所有路径从服务端认证上下文重解析 tenant/user；order/quote 属主校验，
仅凭 id 一律拒绝（不区分「不存在/非本人」，防探测）；前端金额/币种/
provider 字段零信任（服务端重读 + order↔quote 互检）。B15 探针 +
E2E 证据（G31/G32）。

## 21. PII / Secret Safety

Fake 流程零 PII 字段；webhook secret 仅 env；metrics 标签白名单
（provider/operation/status/from_state/to_state/outcome/capability/reason）
——禁 order_id/quote_id/user_id/tenant_id/provider_order_id（G33/T12=0）。

## 22. Metrics / Trace / Audit

`travel_booking_requests_total` / `state_transition_total`（非法转换
illegal: 前缀单列）/ `provider_calls_total` / `idempotency_total` /
`in_doubt_total` / `reconciliation_total` / `webhook_total` /
`recovery_total` / `latency_seconds`——九指标低基数（G35）；
trace 携带 booking.status（内部标识按既有 trace 安全规范）；事件账见 §19。

## 23. Database Constraints

migration `052_travel_booking.sql`（memory 库，travel schema 四表，已登记
MIGRATION_TARGETS）：orders 三 UNIQUE（intent/merchant_order/idempotency_key）
+ webhook (provider,event_id) UNIQUE + events event_id UNIQUE + Quote 不可变
trigger + 恢复扫描部分索引。并发防重靠约束 + ON CONFLICT + 条件 UPDATE，
不靠 Python if-exists（G13）。

## 24. Concurrency Evidence

10/50（pytest parametrize）+ 10（评测探针）相同请求并发：结果 = 1 intent /
1 order / 1 logical provider order / 全部结局收敛合法终态（booked/
already_booked/in_progress/not_confirmable）。实测通过（§四十四）。
（50/100 规模：50 实测于 pytest；100 与 50 同机制——唯一约束与账本 PK
决胜不随并发数变化，如实记录未单独跑 100。）

## 25. Fault Injection

- provider 成功 + 本地 finalize 失败（§四十五）：ledger 已 SUCCEEDED →
  重入直接 replay → BOOKED 零新增（盲二次 create=0）
- provider 成功 + BOOKED 回滚丢失 → 恢复扫描收敛
- UNKNOWN 三模型分派：A 重放 / B 先查 / C IN_DOUBT+人工
- DB 故障：pytest.importorskip 语义（PG 不可达 skip，不假绿）

## 26. Booking Evaluation

`python -m backend.evaluation travel-booking`：**18/18（100%）**，
T1-T12 全达标（T1-T10=0，T11=1.0，T12=0）。覆盖 B1/B2/B4/B6/B7/B8/B9/
B10/B11/B12/B13/B15/B16/B17/B18/B19/B20 + §四十四并发（B3/B5/B14 由
B2/B6/B13 的等价断言覆盖，数据集注明）。

## 27. STOP I Regression

`travel` 评测：33 pass + 1 skip（T-G10 offline-only 声明）+ **0 失败**；
Q10 unsupported_fact=0.0——Q1-Q10 零回归（G37）。

## 28. STOP J Regression

`travel-provider` 评测：**8/8（100%）**。其中 P-07（quota 软停探针）发现
**环境敏感缺陷**：原探针只打桩 `_redis_incr`，Redis 可达时计数永不增长
（通过与否取决于 Redis 不可用的偶然退化）——已补桩 `_redis_get` 强制
进程内计数路径（确定性化，commit `c322ac3`；探针文件属评测基建，
非冻结 Provider 层）。修复后 8/8 稳定复现（G38）。

## 29. STOP K Regression

`travel-commerce` 评测：**26/26（100%）**，C1-C12 全达标；commerce 测试
套件（tests/travel/commerce，111）全绿——commerce 契约/域图/渲染纪律
零改动（booking prefilter 加法插入不改变既有四域两两顺序，G39/G1）。

## 30. Baseline vs Final Regression Delta

**口径（§五十/§五十一，本轮冻结）**：`pytest tests/ -q --no-cov` exit=1 →
禁止写 full regression PASS/0 failed。如实输出：

```text
OWNED_SCOPE_REGRESSION_PASS=true
GLOBAL_REGRESSION_PASS=false
GLOBAL_REGRESSION_BLOCKED_BY_BASELINE_FAILURES=true
```

**命令与集合**：

| 集合 | 结果 |
|------|------|
| BASELINE（开工 HEAD `7f0d0b2` 工作区实测，`/d/tmp/stopl_baseline_pytest.log`） | 104 failed / 6962 passed / 3 errors |
| FINAL（终验 HEAD 实测，`/d/tmp/stopl_final_pytest.log`） | 118 failed / 6991 passed / 3 errors |
| persistent（交集） | 104 |
| resolved（baseline−final） | 0 |
| new（final−baseline） | 14 |

**new=14 的逐项归因（含机制证据，非口头归因）**：

| 项 | 归因 | 证据 | 状态 |
|----|------|------|------|
| `test_phase2_runtime_contract::test_c1_every_registered_task_routes_via_queue_router` | **STOP L 机制性新增**（新 beat 任务的模块 import 了错误的 celery 实例名） | ImportError 栈 `travel_booking_tasks.py:12` | **已修复**（`celery_app` 正名）并隔离重跑验证：tests/test_phase2_runtime_contract.py + booking 套件 56/56 全绿；该测试不在当前失败集 |
| `test_confirmation_store` ×7 + `test_defect6_action_chain` ×6 | **并行会话未提交 WIP 引入**（其 dirty 的 confirmation_store.py 向 save() 传 `tenant_id=` kwarg，测试 mock 未同步） | `git diff backend/customer_service/confirmation_store.py` 含 `+tenant_id=identity.tenant_id`；HEAD 版本 grep tenant_id=0；该 5 文件 + business_guard.py 至今 M/?? 状态 | 等该会话落库后复核；非本轮文件（本轮零触碰 CS） |

**STOP_L_NEW_REGRESSION_COUNT=0**（机制性新增唯一项已修复并验证；余 13
项失败机制全部位于并行会话未提交文件内，本轮 diff 零触碰——落库后须
复跑复核，登记为跨会话待办）。

**OWNED_SCOPE 回归**（tests/travel + tests/orchestration + 六个契约/门禁
文件，1311 tests）：**1311 passed / 2 failed**——2 例为
`test_run_context`（checkpoint namespace 领域，**基线集合内既有**
persistent，非本轮）。OWNED_SCOPE_REGRESSION_PASS=true。

## 31. Gateway E2E

`backend/scripts/e2e_travel_booking.py`（app 镜像重建；APISIX :9080 + JWT
（login 619ms）+ /chat/stream SSE；迁移 052 预先应用容器库；
.env 临时注入 TRAVEL_BOOKING_ENABLED=true + fake_booking_native +
commerce fake + 白名单，用后还原 grep=0）：

```text
L-E2E-1 Quote       PASS  预订确认卡片：42000 JPY/税费未知明示/观测时间/
                          「提交预订前会再次核验」（未承诺锁价）；PG=
                          awaiting_confirmation + merchant_order_id
L-E2E-2 Confirmation PASS 「确认预订」→ 预订成功 + 模拟交易披露（§三十八
                          要求项）；PG=booked + provider_order_id 回执
L-E2E-3 Duplicate    PASS  重复确认 →「该订单已预订成功，无需重复提交」；
                          provider_order_id 不变（幂等收口）
L-E2E-4 Price Change PASS（离线门）执行期价格复核阻断在评测 B11
                          （create=0）与 pytest 钉死；网关层 fake 供给
                          无法安全注入价格漂移，如实以离线证据承担
L-E2E-5 Timeout      PASS（离线门）IN_DOUBT 话术 + 零盲重试由 B6/B10 +
                          pytest 钉死；同上如实以离线证据承担
L-E2E-6 Recovery     PASS（离线门）B20/§四十五（beat 扫描收敛）
L-E2E-7 Tenant       PASS（离线门）B15 fail-closed（网关层单账号体系
                          无法构造双租户，如实以离线证据承担）
L-E2E-8 主链回归     PASS  厦门行程照常出单 + 酒店搜索照常渲染
                          （booking 不干扰 travel/commerce）
STOP_L_GATEWAY_E2E_PASS=true（L-E2E-1/2/3/8 网关实测；
L-E2E-4/5/6/7 由评测+pytest 离线证据承担并如实标注）
```

（共用栈纪律：只重建 app；postgres/redis/APISIX/worker/beat 未动；.env
已还原（grep 注入变量=0）；容器库 booking 表已 TRUNCATE；`/health` 恢复
travel_commerce=disabled。）

## 32. Real Provider Evidence

**不存在**（BLOCKED）。全仓无任何 Booking Provider 凭据（K0/L0 审计）；
`TRAVEL_BOOKING_PROVIDER` 配置真实供应商名 = ProviderContractMissing
fail-closed。G45/G46 未过，如实输出 BLOCKED_BY_EXTERNAL_BOOKING_PROVIDER。

## 33. Files Changed

| 提交 | 内容 |
|------|------|
| 2b856b2 | L0 审计与设计 |
| f6566c6 | L1-L9：migration 052；providers/travel/booking/（包升格：contracts/fake/__init__，删除预留 booking.py）；travel/booking/ 16 模块（identity/state/store/revalidate/executor/authorization/telemetry/service/provider_capabilities/reconciliation/recovery/webhook/reporter/graph_* /register）；config/travel_booking.py；booking_prefilter.py + router_node 插入；domains/queue_router/celery_app/init_db 四处加法式登记；tasks/travel_booking_tasks.py；tests/travel/booking/（43 测） |
| 5eab121 | L8 评测（runner + 18 case 数据集 + 装配） |
| c322ac3 | P-07 探针确定性修复 |
| 本报告提交 | 最终验收报告 + L-E2E 驱动（e2e_travel_booking.py）+ celery 实例名修正（travel_booking_tasks） |

## 34. Commits

`2b856b2` → `f6566c6` → `5eab121` → `c322ac3` → 本报告（§33）。

## 35. Parallel Workspace Protection

- 开工记录他人 WIP（travel conftest.py + 2 docs + 3 未跟踪发布脚本）；
  全程双重 pathspec，未触碰未收编。
- 全量 pytest 期间 backend 冻结（基线与终验两次采集均遵守）。
- 共享栈：只重建 app；.env 临时注入用后还原（grep=0）；容器库 booking
  表清理；宿主 Redis 零写入（评测/测试的 ledger 用独立表）。
- bake 前预检：1637+ 文件 py_compile + app import + 五域注册。

## 36. Deferred（非阻塞）

1. **真实 Booking Provider 适配器**：凭据落地后按 fake 同契约实现
   （登记 registry 证据 → 解 activation gate），G45/G46 补真实 E2E。
2. Cancel/Refund/Payment 生命周期：STOP M。
3. booking_events relay 接入（outbox 模式已就绪：pending→published 列
   与 event_id 去重已建）。
4. 100 并发规模单独压测（10/50 已过；机制不随规模变化）。
5. PII 字段：真实供应商契约明确最低要求后再建（§三十四）。

## 37. Final Gates（G1-G46）

| Gate | 判定 | 证据 |
|------|------|------|
| G1 I/J/K 冻结面无破坏 | ✅ | §27-§29 三套评测全绿；prefilter 加法插入不改变既有顺序 |
| G2 无第二套幂等 | ✅ | executor 装配 shared/idempotency（§12）；全仓无新账本表 |
| G3 无第二套确认系统 | ✅ | 确认=订单状态机内嵌 gate（L0 §3）；CS 设施零触碰 |
| G4 无第二套 Outbox | ✅ | CS outbox 模式复用、域内建账（L0 §5） |
| G5 Quote 不可变 | ✅ | DB trigger + 测试（migration 052） |
| G6 Quote fingerprint 稳定 | ✅ | sha256 + 跨进程随机 PYTHONHASHSEED 复算 |
| G7 Quote expiry 正确 | ✅ | 双时钟分立 + B13 + 恢复兜底 |
| G8 Confirmation 精确绑定 | ✅ | 绑定指纹 = 全部订单事实（§9） |
| G9 价格变化强制重确认 | ✅ | B11 create=0 + order↔quote 互检（T6=0） |
| G10 库存变化禁 Create | ✅ | B12 + offer_gone/unavailable 三路 create=0 |
| G11 BookingIntent 稳定 | ✅ | uuid5 确定性派生（§11） |
| G12 idempotency key 稳定 | ✅ | derive_provider_key（冻结层） |
| G13 DB 唯一约束防并发 | ✅ | 三 UNIQUE + 并发实测 |
| G14 能力模型明确 | ✅ | 集中 registry + 三 fake profile + UNKNOWN fail-closed |
| G15 native replay | ✅ | B8 + B7（同 key 返回既有订单） |
| G16 client-ref lookup-first | ✅ | B9（对账收敛 BOOKED，零重发） |
| G17 无幂等 timeout→IN_DOUBT | ✅ | B10 + T5=0 |
| G18 外呼不占长事务 | ✅ | prepare CAS → 外呼 → finalize CAS（§5） |
| G19 W1-W7 全有恢复策略 | ✅ | §14 表 + 探针 |
| G20 状态机集中 | ✅ | transition 白名单唯一入口（state.py/store CAS） |
| G21 非法转换 fail-closed | ✅ | IllegalBookingTransition + illegal: 指标 |
| G22 状态不倒退 | ✅ | 版本单调 + 终态守卫（B17） |
| G23 success+crash 可恢复 | ✅ | B7 + §四十五 |
| G24 双击不重复 | ✅ | B2 + 并发实测 |
| G25 HTTP retry 不重复 | ✅ | B4（ledger replay） |
| G26 worker retry 不重复 | ✅ | B4/§四十五 |
| G27 webhook 签名/重放防护 | ✅ | B18 + §18 校验链 |
| G28 重复 webhook 幂等 | ✅ | B16（T8=0） |
| G29 未知 webhook fail-closed | ✅ | B19 quarantine |
| G30 Reconciliation≠Retry | ✅ | §16 语义分离（模块级隔离） |
| G31 授权完整 | ✅ | B15 + 属主校验三入口 |
| G32 前端字段不可信 | ✅ | 服务端重读 + order↔quote 互检 |
| G33 PII/secret 不泄漏 | ✅ | 零 PII 字段 + 标签白名单（T12=0） |
| G34 Audit coverage | ✅ | §41 清单全事件（T11=1.0） |
| G35 metrics 低基数 | ✅ | §22 标签白名单 |
| G36 travel-booking 全绿 | ✅ | 18/18 + T 门全达标 |
| G37 STOP I 零回归 | ✅ | 33+1skip+0 fail |
| G38 STOP J 零回归 | ✅ | 8/8（P-07 确定性化后） |
| G39 STOP K 零回归 | ✅ | 26/26 + C1-C12 全达标 |
| G40 NEW_REGRESSION_COUNT=0 | ✅* | §30：机制性新增唯一项（test_c1）已修复+隔离验证；GLOBAL_REGRESSION_PASS=false 如实申报（exit=1：104 persistent + 13 并行 WIP），Delta 复核登记为跨会话待办 |
| G41 网关 E2E | ✅ | §31（1/2/3/8 实测 + 4/5/6/7 离线门如实标注） |
| G42 并发测试 | ✅ | §24（10/50 实测） |
| G43 故障注入 | ✅ | §25 |
| G44 parallel clean | ✅ | §35 |
| G45 Real Booking E2E | ❌ BLOCKED | 无凭据（§32） |
| G46 Real replay/query E2E | ❌ BLOCKED | 同上 |

## 38. Frozen Boundary

```text
TRAVEL_BOOKING_TRANSACTION_CORE_FROZEN=true
```

冻结：BookingOrder 状态机（状态集/白名单/版本单调/IN_DOUBT 出口语义）、
幂等语义（intent/merchant_order/ledger key 派生规则 + 接管策略）、确认
语义（绑定指纹/原子认领/TTL）、Quote 契约（字段/不可变/指纹）、Webhook
Inbox（校验链/隔离语义）、Reconciliation/Recovery 语义、审计事件清单、
Fake/Sandbox Adapter 契约（三能力 profile 与 registry 条目）、travel
schema 四表结构（migration 052）。

演进约束：真实供应商 = 新适配器 + registry 取证登记 + G45/G46 实机；
状态机新增状态/转换须先改本文；STOP M（Payment/Cancel/Refund/Change）
在 booking.order 终态之上展开，不改写本轮冻结语义。

**生产闭环判定条件**：G45/G46 需真实 Booking Provider。当前如实输出
§1 Verdict——核心架构完成 ≠ 生产 Booking 可用（§四十八）。
