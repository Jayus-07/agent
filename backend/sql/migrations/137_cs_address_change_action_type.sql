-- B7（cs-capability-gap）：允许在途订单改址动作写入客服审计记录。
-- 保留既有退款、售后、催办和价保动作类型，防止约束收窄造成历史能力回退。
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
        'urge_request', 'price_match', 'address_change'
    ));
