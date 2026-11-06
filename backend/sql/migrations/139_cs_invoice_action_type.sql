-- B8（cs-capability-gap）：允许模拟开票申请写入客服 agent_actions 审计。
-- 本迁移目标为 memory 库；业务表位于 agent_business 的 138 迁移。
-- 保留 B4-B7 及既有动作类型，避免约束收窄造成历史能力回退。
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
        'urge_request', 'price_match', 'address_change', 'invoice_request'
    ));
