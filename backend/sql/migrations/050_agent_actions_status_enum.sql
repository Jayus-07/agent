-- 050_agent_actions_status_enum.sql — agent_actions.status 枚举对齐应用契约
-- 背景（2026-09-24 Side-Effect 幂等人工生产验收 STOP F1 实机发现）：
--   AgentActionRecord.status 应用侧契约 = "simulated" | "executed" | "failed"
--   （backend/customer_service/action.py），而 006 迁移的
--   agent_actions_status_check 只允许 pending/approved/executing/success/
--   failed/cancelled——交集仅 failed。cs_graph_node 的同事务审计落库
--   （insert_action_idempotently → insert_agent_action）在真实确认动作上
--   必然 CheckViolation → 整笔审计事务回滚 → agent_actions 生产表零落库
--   （2026-09-24 实测复现，此前被 executed_at 字符串 DataError 先行掩盖）。
-- 决策：扩 DB 枚举（additive、向后兼容）而非代码侧把 simulated 映射成
--   success——审计必须如实记录「本次为模拟执行」，不得粉饰成真实成功。
-- 回滚：DROP 新约束并重建为 006 原义六值枚举。

ALTER TABLE customer_service.agent_actions
    DROP CONSTRAINT IF EXISTS agent_actions_status_check;

ALTER TABLE customer_service.agent_actions
    ADD CONSTRAINT agent_actions_status_check
    CHECK ((status)::text = ANY (ARRAY[
        'pending'::character varying,
        'approved'::character varying,
        'executing'::character varying,
        'success'::character varying,
        'failed'::character varying,
        'cancelled'::character varying,
        'simulated'::character varying,
        'executed'::character varying
    ]));
