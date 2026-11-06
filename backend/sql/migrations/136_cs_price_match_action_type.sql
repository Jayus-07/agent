-- B6（cs-capability-gap）：把价保 mock 动作纳入客服 agent_actions 审计。
-- 资金动作仍只写入 business-mock 演示钱包；本迁移不授权真实退款。
-- 保留 B5 已登记的 urge_request，避免编号顺序执行时后续迁移收窄约束。
ALTER TABLE customer_service.agent_actions
    DROP CONSTRAINT IF EXISTS agent_actions_action_type_check;

ALTER TABLE customer_service.agent_actions
    ADD CONSTRAINT agent_actions_action_type_check
    CHECK (action_type IN (
        'refund_request', 'refund_execute',
        'return_request', 'return_execute', 'exchange_request',
        'address_update', 'password_reset', 'order_cancel',
        'complaint_create', 'handoff_request',
        'handoff_accept', 'handoff_close',
        'urge_request', 'price_match'
    ));
