-- 051_cs_business_operation_guard.sql（target: memory）
-- Phase3 STOP D（Business Entity Unique Guard）：
--   跨 confirmation 的语义等价业务操作唯一性 —— 同一 tenant + 同一
--   action + 同一业务实体 + 同一 semantic payload 的 active 操作全局
--   只允许一行。正确性由 PostgreSQL partial unique index 保障
--   （并发 INSERT 由唯一索引决胜，禁止先查后插）。
--
--   1) confirmations 补业务操作身份两列（nullable —— legacy 行不回填、
--      不进索引；新生产写入由 service 层 fail-closed 强制完整身份，
--      见 business_guard.py）：
--        tenant_id           可信身份链（runner JWT → cs_context），
--                            修 STOP A 确认的缺租户问题；
--        semantic_fingerprint 语义载荷 SHA-256（business_guard 生成，
--                            排除 confirmation_id/execution_id/时间戳/
--                            措辞等非业务字段）。
--   2) state CHECK 收编 'verifying'（既有 ConfirmationState.VERIFYING，
--      2026-09-22 Tool 治理引入的「结果未知待对账」语义）——STOP C 的
--      SideEffectOutcomeUnknown 落库到该态后继续占住唯一索引
--      （IN_DOUBT 不得释放，防绕过 Phase2/STOP C 幂等）。
--   3) active 语义操作 partial unique index：
--        WHERE state IN (pending/confirmed/executing/verifying)
--          AND 完整身份（tenant/fingerprint 非空、target_id 非空——
--          need_info 补槽占位行不参与守卫，升级为正式 proposal 时
--          原子补全身份并接受约束）。
--
--   存量核查（2026-09-24 实库 5433）：24 行（2 cancelled / 8 success /
--   14 expired），零 active 行 —— 迁移无存量冲突风险；legacy 行保持
--   NULL 身份不参与索引（不伪造回填）。

ALTER TABLE customer_service.confirmations
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64);
ALTER TABLE customer_service.confirmations
    ADD COLUMN IF NOT EXISTS semantic_fingerprint VARCHAR(64);

ALTER TABLE customer_service.confirmations
    DROP CONSTRAINT IF EXISTS confirmations_state_check;
ALTER TABLE customer_service.confirmations
    ADD CONSTRAINT confirmations_state_check
    CHECK (state IN (
        'pending', 'confirmed', 'cancelled',
        'executing', 'verifying', 'success', 'failed', 'expired'
    ));

CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_confirmations_active_biz_op
    ON customer_service.confirmations (
        tenant_id, action_type, target_type, target_id, semantic_fingerprint
    )
    WHERE state IN ('pending', 'confirmed', 'executing', 'verifying')
      AND tenant_id IS NOT NULL
      AND semantic_fingerprint IS NOT NULL
      AND target_id <> '';
