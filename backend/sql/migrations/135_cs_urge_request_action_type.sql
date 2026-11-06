-- B5（cs-capability-gap）：agent_actions 收录低风险物流催办动作审计。
-- 催办业务请求只写入 business-mock；agent_actions/audit_logs 复用既有
-- CS Graph 审计事务与幂等 ledger，不新增业务表。
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
        'urge_request'
    ));
