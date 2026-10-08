-- 081_auth_user_phone.sql — 用户手机号（目标库：agent_memory）
--
-- 背景（2026-10-08 拍板）：转人工超时降级的回访话术要「确认用户手机号」，
-- 此前 auth.users 无 phone 列只能引导用户手输。本迁移补齐字段：
--   注册页可选收集（auth_local.register）→ reaper 降级话术带出打码号码
--   供用户确认（「回访 138****8000 可以吗」）。
-- 宽松校验（应用层）：11 位中国大陆手机号或空；历史账号允许为空。

ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS phone VARCHAR(20) NOT NULL DEFAULT '';

COMMENT ON COLUMN auth.users.phone IS
    '回访手机号（可选，11 位大陆手机号；空=未提供，超时回访话术引导用户补充）';
