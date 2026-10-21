-- 103_auth_department_version.sql
-- 部门编辑/停用使用版本号 CAS，避免并发管理员覆盖对方的更新。

ALTER TABLE auth.departments
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN auth.departments.version IS
    '部门管理乐观锁版本；每次名称或状态更新递增';
