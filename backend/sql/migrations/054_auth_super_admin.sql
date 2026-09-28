-- 054_auth_super_admin.sql — 扩展静态平台角色，保留既有用户角色值。
-- super_admin 为 11 个字符；先扩容再替换约束，脚本可安全重复执行。

ALTER TABLE auth.users
    ALTER COLUMN role TYPE VARCHAR(11);

ALTER TABLE auth.users
    DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE auth.users
    ADD CONSTRAINT ck_users_role
    CHECK (role IN ('viewer', 'editor', 'admin', 'super_admin'));
