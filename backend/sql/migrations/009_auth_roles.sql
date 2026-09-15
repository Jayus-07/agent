-- 009_auth_roles.sql — 用户角色（对齐 prompts.py::_check_permission 权限矩阵）
-- 枚举：viewer（只读）/ editor（草稿+发布）/ admin（含 high 风险发布与回滚）
-- 首个 admin 由 DBA 手工提升：
--   UPDATE auth.users SET role='admin' WHERE username='<用户名>';
-- 公开注册端点固定写 viewer，不暴露 role 参数

ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS role VARCHAR(10) NOT NULL DEFAULT 'viewer';

ALTER TABLE auth.users
    DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE auth.users
    ADD CONSTRAINT ck_users_role CHECK (role IN ('viewer', 'editor', 'admin'));
