-- 113_security_events_admin_action.sql — 安全事件表扩展 ADMIN_ACTION（治理优化）
-- 目标：管理端主动动作（如 Tool 失败探针）此前只有 synthetic trace 留痕，
--   无操作审计记录——「谁在什么时候执行了什么管理动作」不可追溯。
--   ADMIN_ACTION 与拒绝类事件（AUTHZ_DENIED 等）共用 security_events 落点：
--   旁路追加、软失败、detail 只存脱敏摘要。
-- 幂等：DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT，重复执行结果一致。
-- 回滚：ALTER TABLE ... DROP CONSTRAINT ck_security_events_type,
--       ADD CONSTRAINT ... CHECK (不含 'ADMIN_ACTION')（见文件尾注释）。

ALTER TABLE ai.security_events
    DROP CONSTRAINT IF EXISTS ck_security_events_type;

ALTER TABLE ai.security_events
    ADD CONSTRAINT ck_security_events_type CHECK (event_type IN (
        'INPUT_GUARD_BLOCK', 'AUTHZ_DENIED', 'JWT_INVALID',
        'EVIDENCE_REJECT', 'GATEWAY_DENIED', 'ADMIN_ACTION'));

-- 回滚：
-- ALTER TABLE ai.security_events DROP CONSTRAINT ck_security_events_type;
-- ALTER TABLE ai.security_events ADD CONSTRAINT ck_security_events_type CHECK (event_type IN (
--     'INPUT_GUARD_BLOCK', 'AUTHZ_DENIED', 'JWT_INVALID',
--     'EVIDENCE_REJECT', 'GATEWAY_DENIED'));
