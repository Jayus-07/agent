-- 152_retire_crawler_read_access.sql
-- 竞品采集域已退役：保留旧表和数据，但问数只读账号不再拥有读取权。
-- 需排在 004_readonly_role 和 148_readonly_default_privileges_completion 之后。
BEGIN;

REVOKE SELECT ON ALL TABLES IN SCHEMA crawler FROM agent_readonly;
REVOKE USAGE ON SCHEMA crawler FROM agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA crawler
    REVOKE ALL ON TABLES FROM agent_readonly;

COMMIT;
