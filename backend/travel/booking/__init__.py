"""backend/travel/booking — Booking Transaction & Order Lifecycle（STOP L）

生产闭环（L0 定稿）：Quote（不可变快照）→ 显式确认（绑定指纹）→ 价格/
库存复核 → 幂等 Booking Create（复用全局 PG 幂等账本 + Phase3 三模型
Provider 契约）→ 订单状态机（集中转换/版本单调）→ Webhook Inbox /
Reconciliation / Recovery → 审计事件。

**同一业务意图最多执行一次外部 Booking 副作用**；无法证明成败时 IN_DOUBT，
绝不猜 SUCCESS、绝不盲目重试。
"""
