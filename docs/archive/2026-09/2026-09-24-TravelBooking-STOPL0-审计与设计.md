# Travel Booking Transaction — STOP L0 审计与设计

> 日期：2026-09-24 ｜ 基线：STOP K 冻结（`7f0d0b2`，TRAVEL_COMMERCE_CONTRACT_FROZEN=true）
> 本文是 STOP L 的唯一设计事实源；实现（L1-L10）不得偏离本文。

---

## 1. Frozen Baseline

STOP I（行程质量）/ STOP J（Provider 层）/ STOP K（Commerce 契约，
`TRAVEL_COMMERCE_CONTRACT_FROZEN=true`，STOP_K_PASS=false +
BLOCKED_BY_EXTERNAL_PROVIDER=true **保持不变**，§四十九）。
Phase2 异步运行时 / Step6 副作用幂等 / Phase3 STOP C Provider 幂等契约均
已冻结——本轮是它们的**消费者**，不是改造者。

## 2. Existing Side-effect Infrastructure（§五.A：已有，必须复用）

`backend/shared/idempotency.py`（Phase2 Step6 + Phase3 持续收口，已冻结）：

| 设施 | 语义 |
|------|------|
| `IdempotencyKey(tenant, actor, operation, client_key)` | 全局幂等键（tenant 前置隔离） |
| `PostgresIdempotencyLedgerStore` | PG 权威账本 `ai.idempotency_records`（migration 031/047）：INSERT ON CONFLICT 原子抢注、lease、`owner_execution_id` CAS、接管判定（failed 可重试；running+租约过期须 `takeover_allowed` 判定，否则保守 UNCERTAIN） |
| `run_idempotent_side_effect` | 无 HTTP 上下文副作用入口（任务/域图语义） |
| `SideEffectOutcomeUnknown` | 副作用边界越过但结果未知 → UNCERTAIN 保守阻断，禁止同 key 自动重试 |
| `resolve_stale_side_effect` | stale claim 人工裁决（executed / not_executed） |
| `execute_idempotent_in_transaction` | DB 内部副作用同事务原子收口（booking 不用——外部调用禁入长事务，§G18） |

**结论：禁止新建 `travel_booking_idempotency` 第二套账本（G2）。** Booking
executor 全部走 `run_idempotent_side_effect`，client_key=merchant_order_id
（确定性派生）。

## 3. Existing Confirmation Infrastructure（§五.B：CS 域内单一实现，非跨域设施）

`backend/customer_service/confirmation.py`（状态机）+ `confirmation_flow.py`
（单一入口）+ `confirmation_store.py`（DB 条件 UPDATE 原子认领）：

- 状态机 PENDING→CONFIRMED→EXECUTING→SUCCESS/FAILED（+VERIFYING 对账态）、
  PENDING→CANCELLED/EXPIRED；认领 = 原子条件更新防重复提交。
- **耦合面**：按 CS 会话键（user_id+session_id）认领、CS `BusinessRuleError`
  错误类型、CS pending_action 形状（action_type=CS 工具）、async SQLAlchemy
  CS 仓储。它不是跨域通用设施（无 quote/金额/币种/供应商绑定维度）。

**G3 判定口径**：不为 Travel 新建「独立确认状态机实体」，也不把 CS 会话键
设施错接到 booking。**用户确认实现为 BookingOrder 状态机的内嵌 gate**：
`AWAITING_CONFIRMATION → CONFIRMED` 转换必须携带确认绑定快照
（quote_id + quote_fingerprint + amount + currency + provider + operation +
tenant + user + internal_expires_at 的 canonical 指纹）与 DB 条件更新原子
认领（复用与 CS `claim_for_execution` 同一模式：条件 UPDATE 防重复确认）。
即：确认不是第二套状态机，而是业务订单状态机的一组受保护转换。

## 4. Existing Task/Recovery Infrastructure（§五.C：已有，必须复用）

Celery 双队列→五队列拓扑（Phase1/2 冻结）：状态权威在 PG、lease/execution_id、
`tasks.pending_recovery` / `tasks.zombie_reconcile` / `tasks.stale_execution_recovery`
/ `tasks.idempotency_retention` 等 maintenance beat 任务、QueueRouter fail-closed
单一事实源（`_BEAT_TASK_ROUTES` 显式登记）。

**Booking 复用方式**：外部副作用执行状态全部落 PG（order + idempotency
ledger），不依赖请求进程内存；恢复通过新增 maintenance 任务
`travel.booking_recovery_scan`（登记 maintenance 队列 + beat 显式路由）周期
扫描 stale SUBMITTING / IN_DOUBT / 到期订单，按 provider 能力走
reconcile/重执行/保持 IN_DOUBT（§13-§16）。beat 挂载 = 对冻结路由表的
**加法式登记**（新增键，不改既有键）。

## 5. Existing Outbox（§五.D：CS 域有完整 PG transactional outbox，schema 专用）

- `customer_service.events`（migration 028）+ `dispatch/outbox.py`：**完整的
  transactional outbox 模式**——`append_event` 与业务写同事务落库
  （outbox_status='pending'）→ worker `FOR UPDATE SKIP LOCKED` 扫描 relay →
  成功才标 published；`event_id` UNIQUE 去重；lag 指标 + cs-dispatcher 容器。
  另有 `event_outbox.py`（Redis Stream）作 SSE 送达补偿——两层职责不同。
- **耦合面**：表在 customer_service schema、封套绑定 CSEvent（conversation
  形状）。跨域复用 = 把 travel 事件写进 CS schema 或抽象重构 CS 冻结代码，
  两者皆劣于模式复用。
- 全仓无 webhook 入站设施（唯一近似 = Kafka consumer 的 Redis SETNX 去重，
  非 HTTP inbox）；merchant_order/external_reference/provider_order_id 全仓
  零命中（本轮全新建设）；无通用跨域 audit 设施（CS audit_logs 形态最完整：
  build_audit_entry 构建器 + 「关键旁路」写入策略；另有 business_guard 四层
  身份模型（业务操作身份/confirmation_id/ledger key/provider key，migration
  051 semantic_fingerprint partial unique）——**可复用的设计**）。

**G4 判定口径**：booking 事件采用 **CS outbox 的同一模式**（同事务 append +
event_id 唯一去重 + outbox_status pending→published 可 relay），落
`travel.booking_events`（域内审计事件账，§四十一事件清单，与状态转换同事务）。
不是第二套自造 outbox 系统——是既有模式的域内实例化；通用化抽象 CS 表的
重构风险（冻结面 + 并行会话）大于收益，如实记录。

**身份分层沿用 business_guard 四层模型**：业务操作身份=BookingIntent/
merchant_order_id；confirmation=BookingOrder 确认 gate 绑定指纹；幂等
ledger key=merchant_order_id；provider key=derive_provider_key 派生。

## 6. Provider Booking Capability Matrix（§五.E）

复用 `backend/shared/provider_idempotency.py`（Phase3 STOP C 冻结）：
`ProviderIdempotencyCapabilities`（集中 registry `PROVIDER_CONTRACTS` 唯一
事实源、UNKNOWN fail-closed、`derive_provider_key` 确定性派生、
`decide_outcome_action` 三模型决策矩阵 = STOP L §十七 Model A/B/C）、
`reconcile_provider_effect`（结构化 lookup 结论）。

**Booking Provider 登记（本轮新增条目，加法式）**：

| provider | native idempotency | client ref | lookup | webhook | unknown policy | 证据 |
|----------|-------------------|-----------|--------|---------|----------------|------|
| `fake_booking_native`（Model A） | SUPPORTED（Idempotency-Key body） | merchant_order_id | SUPPORTED | SUPPORTED（inbox 注入） | SAFE_RETRY | fake 适配器实现（同 key 同 payload 返回同一订单，测试钉死） |
| `fake_booking_clientref`（Model B） | UNSUPPORTED（无键载体） | merchant_order_id | SUPPORTED（按 ref 查询） | UNSUPPORTED | RECONCILE_FIRST | fake 适配器实现 |
| `fake_booking_bare`（Model C） | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | IN_DOUBT | fake 适配器实现 |
| 真实 Hotel/Flight booking | **UNKNOWN**（无凭据） | — | — | — | fail-closed（拒绝执行） | 不存在 |

选型由 `TRAVEL_BOOKING_PROVIDER` 配置驱动（默认 off；评测/测试注入三种
fake profile）。真实供应商无凭据 → `ensure_execution_allowed` fail-closed。

## 7. Current Gaps（= 本轮交付物）

1. Booking Quote（不可变快照 + 指纹 + 双时钟 expiry）——无
2. BookingIntent / merchant_order_id / 稳定幂等 key 派生——无
3. BookingOrder 状态机（集中转换 + CAS + 状态版本单调）——无
4. 幂等执行器与 Provider 能力模型消费——无
5. IN_DOUBT / Reconciliation / Recovery——无
6. Webhook Inbox（签名/重放/去重/隔离）——无
7. DB schema（migration 052：quotes/orders/webhook_inbox/events + 唯一约束）——无
8. 授权/租户隔离 + 服务端事实重读——无
9. 可观测（travel_booking_* 低基数指标）+ 审计事件——无
10. 评测 travel-booking（B1-B20/T1-T12）+ 并发/故障注入 + 网关 E2E——无

## 8. Proposed Architecture

```text
用户（帮我订大阪10月3日梅田广场酒店 → 确认预订 → 我的预订状态）
    ↓ APISIX :9080（JWT）
router_node：客服 > 旅游 > 选品 > 【预订（新增，TRAVEL_BOOKING_ENABLED）】 > 商务 > 主 Router
    ↓ route_mode="travel_booking"
travel_booking_graph_node（适配器；无 checkpointer）
    ↓
travel-booking 域图（2 节点线性，零 LLM）
    booking_resolver（子意图：新预订/确认/状态查询）
    → booking_executor（服务调用 + 渲染）
    ↓
travel/booking/（领域层）
    quote.py      不可变 Quote（snapshot + sha256 指纹 + 双时钟 expiry）
    intent.py     BookingIntent / merchant_order_id / idempotency key 派生（uuid5/sha256 确定性）
    state.py      BookingOrder 状态机（集中 transition + CAS + status_version 单调）
    revalidate.py 价格/库存复核（复用 travel.commerce.service 确定性重搜 + Decimal 精确比较）
    service.py    quote/confirm/execute/status 编排
    executor.py   幂等执行（run_idempotent_side_effect + ProviderEffectOutcome 决策矩阵）
    provider_capabilities.py  三类 provider 行为装配（复用 Phase3 契约层）
    reconciliation.py  查询事实 ≠ 重试
    recovery.py   stale SUBMITTING/IN_DOUBT 扫描（beat 任务体）
    webhook.py    Inbox 落库 + 签名/重放/去重/隔离
    authorization.py  tenant/user 属主校验 + 服务端事实重读
    reporter.py   真实状态话术（IN_DOUBT ≠ 失败/成功；禁词）
    telemetry.py  travel_booking_* 低基数指标
    audit.py      travel.booking_events 同事务追加
    ↓
providers/travel/booking/（原 booking.py 预留 Protocol 升格为包；contracts.py
    + fake.py（三能力 profile）+ 存量语义迁入并保持测试钉死行为）
    ↓
shared/idempotency.py（PG ledger，冻结）+ shared/provider_idempotency.py（冻结）
    ↓
（真实供应商：未接入——BLOCKED_BY_EXTERNAL_BOOKING_PROVIDER）
```

**复用而非重建清单**：幂等账本/lease/接管/人工裁决（shared/idempotency）、
Provider 三模型决策矩阵与能力 registry（shared/provider_idempotency）、
确定性重搜（travel.commerce.service，冻结契约）、Deep Link 校验
（travel.commerce.deeplink，payment_redirect 同标准）、Celery maintenance
任务模式与 QueueRouter 登记制、CS 原子认领模式（仅模式复用）。

## 9. State Machine（G20/G21/G22）

```text
QUOTED → AWAITING_CONFIRMATION → CONFIRMED → SUBMITTING → BOOKED
   ↓(expiry)        ↓(expiry)         ↓(price/库存变化阻断)   ↓(provider 明确拒绝)
EXPIRED           EXPIRED              FAILED(cause)        FAILED
                                         SUBMITTING → IN_DOUBT（结果未知，保守）
                                         SUBMITTING → FAILED（NOT_SENT 明确未过界，可重入）
                                         IN_DOUBT → BOOKED / FAILED（仅经 reconciliation/人工裁决）
```

- 集中唯一转换函数 `transition(order_id, from, to, cause, actor)`：白名单
  校验 + `status_version` 单调递增 + 同事务追加 booking_events；非法转换
  fail-closed 抛错。
- 允许增加 `RECONCILING`？——不增加（§十八「不要过度设计」）：对账是
  IN_DOUBT 订单上的**动作**（事件记录），不是状态。
- 单调性：BOOKED/FAILED/EXPIRED 为终态（IN_DOUBT 除外）；迟到 webhook /
  迟到 polling 不得倒退（状态版本 + 终态守卫双重防护）。

## 10. Idempotency Model

- `booking_intent_id = uuid5(NAMESPACE, tenant|user|quote_id|confirmation_fingerprint)`
  ——双击/刷新/retry 构造出同一 intent。
- `merchant_order_id = "MOB-" + sha256(tenant|intent_id|provider)[:20]` ——稳定。
- 幂等 ledger：`operation="travel.booking.create"`，`client_key=merchant_order_id`，
  `actor=user_id`，payload=create 请求 canonical 指纹（provider key 经
  `derive_provider_key` 派生并在 native 模式下作为 Idempotency-Key 传递）。
- DB 唯一约束兜底并发：orders 表 `booking_intent_id`/`merchant_order_id`/
  `idempotency_key` 各建 UNIQUE（Python 检查只是快路径，防并发靠约束 +
  ON CONFLICT 捕获）。

## 11. Crash Windows（G19，逐个恢复策略）

| 窗口 | 场景 | 恢复 |
|------|------|------|
| W1 | claim 成功、provider 未调、进程 crash | ledger running+租约过期：Model A → takeover_allowed(SAFE_RETRY)=true 安全重放；Model B → recovery 扫描先 lookup；Model C → 保持 IN_DOUBT 待人工 |
| W2 | 请求已发出、本地 timeout | adapter 分类 UNKNOWN → `decide_outcome_action`：A=SAFE_RETRY / B=RECONCILE_FIRST / C=IN_DOUBT |
| W3 | provider 已创建、本地未写 BOOKED 即 crash | 同 W1（重放同 key 返回既有订单 / lookup 找到 / IN_DOUBT） |
| W4 | BOOKED 已落库、响应未达客户端 | 事实已终态：状态查询/重发渲染，不产生新副作用 |
| W5 | webhook 早于 HTTP response | inbox 先落库；应用时订单可能仍 SUBMITTING → 转换按白名单（SUBMITTING→BOOKED 合法），HTTP 收尾按状态幂等收口 |
| W6 | webhook 重复 | inbox event_id 唯一约束 + 应用幂等（同事件重放零增量效果） |
| W7 | webhook 乱序 | 状态版本单调 + 终态守卫：旧状态事件不倒退新状态 |

## 12. Webhook Model

`travel.booking_webhook_inbox`（event_id UNIQUE(provider,event_id)）：
received→processed/quarantined/ignored。安全：HMAC-SHA256 签名 + 时间戳
重放窗口（±5min）+ provider allowlist + payload ≤64KB + content-type 白名单；
secret 仅 env，禁日志/trace/响应。应用规则：按 provider_order_id/merchant ref
找单，找不到 → quarantine（不 attach 最近订单，§三十）。fake provider 的
webhook 以「inbox 注入函数」模拟（仅测试/评测）。

## 13. Reconciliation Model（G30：查询事实 ≠ 重试）

`reconciliation.reconcile(order)`：仅对 IN_DOUBT / stale SUBMITTING；经
`reconcile_provider_effect` 结构化结论：KNOWN_SUCCESS→BOOKED；
KNOWN_FAILURE→FAILED；NOT_FOUND_SAFE_TO_RETRY→清除 ledger UNCERTAIN
（`resolve_stale_side_effect(not_executed)`）后允许重入 executor；
IN_DOUBT→保持。触发：recovery beat 扫描 + 显式 API；与业务 retry 完全分离。

## 14. Security Model

- 授权：所有路径从服务端认证上下文重解析 tenant/user；order/quote/
  confirmation 属主校验；仅凭 id 读取/执行一律拒绝（G31）。
- 前端不可信：只接受 quote_id/order_id 引用；金额/币种/provider 一律服务端
  重读（G32）。
- PII 最小化：Fake 流程不需要旅客 PII → **本轮零 PII 字段**（§三十四：不为
  未来猜字段）；metrics/trace/log 禁 order_id/quote_id/user_id/tenant_id
  标签（G33，低基数标签白名单）。
- secret：webhook signing key/provider key 仅 env。

## 15. DB Design（migration 052_travel_booking.sql → agent_memory，新 schema travel）

| 表 | 要点 |
|----|------|
| travel.booking_quotes | 不可变快照；价格列 NUMERIC(18,4)+currency；quote_fingerprint；provider_expires_at / internal_expires_at 分立；status(active/expired/superseded/invalidated)；BEFORE UPDATE trigger 拒改价格列（DB 级不可变守卫） |
| travel.booking_orders | §三十二字段全集 + status_version；UNIQUE(booking_intent_id)/UNIQUE(merchant_order_id)/UNIQUE(idempotency_key)；索引 (tenant_id,status,updated_at) 供恢复扫描 |
| travel.booking_webhook_inbox | UNIQUE(provider,event_id)；payload_hash；status |
| travel.booking_events | append-only 审计账（BIGSERIAL + (order_id, seq)）；与状态转换同事务写入 |

登记 `MIGRATION_TARGETS["052_travel_booking.sql"]="memory"`（未登记即
init_db 报错，机制既有）。

## 16. Test Matrix

- 状态机：全转换白名单/非法转换 fail-closed/终态倒退拒绝/版本单调
- Quote：不可变（trigger 级）/指纹稳定（跨进程 sha256）/双时钟 expiry
- 确认：绑定快照指纹/过期/重复确认原子认领/价格变化强制重确认（B11）
- Executor：三类 provider × 五种结局矩阵（SUCCEEDED/NOT_SENT/REJECTED/
  UNKNOWN×能力）/幂等重放/接管策略
- Webhook：签名/重放/重复/乱序/未知订单隔离
- 并发：10/50/100 同请求 → 1 intent/1 order/≤1 create（PG 隔离库实测）
- 故障注入：provider 成功后 finalize 失败（§四十五）
- 评测 travel-booking：B1-B20 + T1-T12

## 17. Runtime E2E Plan

`backend/scripts/e2e_travel_booking.py`（APISIX+JWT+SSE；app 重建 + .env 临时
注入 TRAVEL_BOOKING_ENABLED=true + provider=fake_booking_native；用后还原）：
L-E2E-1 Quote → L-E2E-2 Confirmation → L-E2E-3 Duplicate Click →
L-E2E-4 Price Changed（场景注入）→ L-E2E-5 Timeout Unknown（IN_DOUBT 话术 +
零盲重试）→ L-E2E-6 Recovery（恢复扫描收敛）→ L-E2E-7 Tenant Isolation →
L-E2E-8 规划/酒店/机票主链零干扰。PG 证据：orders/events 行数与状态。

## 18. Scope In / 19. Scope Out

In：§三清单全项（Quote/Intent/Confirmation/Revalidation/Order/Booking
Create/Provider 能力/幂等/External Reference/Webhook Inbox/状态机/IN_DOUBT/
Reconciliation/Recovery/Outbox-Audit/授权租户/可观测/评测/网关 E2E）。

Out（§四原文）：Raw Card/Payment Capture/Refund/Change/Exchange/
Chargeback/Cancel Policy/Loyalty/Invoice/FX。payment_deep_link 仅在
Provider 返回时经 deeplink.sanitize 同标准校验后保存展示，本系统不做
PCI processor。真实支付生命周期留 STOP M。

## 20. Frozen Boundary（本轮对冻结文件的触碰清单，全部加法式）

| 冻结文件 | 触碰 | 理由 |
|----------|------|------|
| `shared/provider_idempotency.py` | PROVIDER_CONTRACTS 增加 3 条 fake booking 契约 | registry 即唯一事实源；未登记 = fail-closed 拒绝执行 |
| `tasks/queue_router.py` | `_BEAT_TASK_ROUTES` 增加 travel.booking_recovery_scan | QueueRouter 显式登记制；maintenance 队列复用 |
| `tasks/celery_app.py` | beat_schedule 增加 recovery 扫描条目 | 同上，加法式 |
| `orchestration/graph/router_node.py` | commerce prefilter 之前插入 booking prefilter | 优先级变为 客服>旅游>选品>预订>商务——既有四域两两顺序不变（加法插入）；订 verbs 是交易意图，不得降级为 search（任务书 §七 调用关系） |
| `domains/__init__.py` | +1 行 booking 域注册 | 既有自注册机制 |
| `providers/travel/booking.py` | 升格为包 `providers/travel/booking/`（存量语义迁入 contracts，`providers/travel/__init__.py` 再导出与 test_booking_candidate import 路径同步更新） | 任务书 §七 指定包形态；booking.py 本身即「Phase 7 预留、STOP L 实现」的资产；行为由既有测试继续钉死 |

不触碰：travel 域图/validator/reporter、commerce 域图拓扑与渲染纪律
（booking 经服务层复用 commerce.service 只读搜索）、CS confirmation 设施、
Celery 既有任务、ai.idempotency_records 表结构。
