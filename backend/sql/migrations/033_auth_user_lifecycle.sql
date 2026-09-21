-- ============================================================
-- 033_auth_user_lifecycle.sql — 用户生命周期字段（P6.3）
--
-- must_change_password：管理员创建用户 / 重置密码后置 TRUE，
-- 用户以临时密码登录后仅允许改密/登出/基本信息接口；
-- 成功修改密码后由 /auth/change-password 置回 FALSE。
-- email：用户详情展示字段（P6.1），存量回填空串。
--
-- 目标库：agent_memory
-- 幂等：ADD COLUMN IF NOT EXISTS，可安全重放。
-- ============================================================

ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS email VARCHAR(255) NOT NULL DEFAULT '';
