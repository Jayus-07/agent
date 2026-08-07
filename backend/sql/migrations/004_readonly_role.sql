-- =====================================================
-- 004_readonly_role.sql
-- 生产就绪 P0: 创建只读角色 + 权限配置
--
-- 设计动机:
--   旧实现用 postgres superuser 执行查询，依赖应用层
--   SET TRANSACTION READ ONLY 防止写入。
--   一旦应用层绕过（如 execute_sql_tool），可任意写库。
--   改为数据库层 role 级别权限控制，双重防线。
--
-- 执行: psql -U postgres -d agent_business -f 004_readonly_role.sql
-- =====================================================

-- ═══ 1. 创建只读角色 ═══
-- NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
-- 密码由环境变量 PG_READONLY_PASSWORD 注入（不硬编码）
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agent_readonly') THEN
        CREATE ROLE agent_readonly WITH LOGIN PASSWORD 'agent_readonly_dev' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- ═══ 2. 库级 CONNECT ═══
GRANT CONNECT ON DATABASE agent_business TO agent_readonly;

-- ═══ 3. Schema 级 USAGE ═══
GRANT USAGE ON SCHEMA product TO agent_readonly;
GRANT USAGE ON SCHEMA "order" TO agent_readonly;
GRANT USAGE ON SCHEMA inventory TO agent_readonly;
GRANT USAGE ON SCHEMA customer TO agent_readonly;
GRANT USAGE ON SCHEMA crawler TO agent_readonly;
GRANT USAGE ON SCHEMA finance TO agent_readonly;
GRANT USAGE ON SCHEMA ai TO agent_readonly;

-- ═══ 4. 现有表 SELECT 权限 ═══
-- product 域
GRANT SELECT ON ALL TABLES IN SCHEMA product TO agent_readonly;
-- order 域（带引号 schema）
GRANT SELECT ON ALL TABLES IN SCHEMA "order" TO agent_readonly;
-- inventory 域
GRANT SELECT ON ALL TABLES IN SCHEMA inventory TO agent_readonly;
-- customer 域
GRANT SELECT ON ALL TABLES IN SCHEMA customer TO agent_readonly;
-- crawler 域
GRANT SELECT ON ALL TABLES IN SCHEMA crawler TO agent_readonly;
-- finance 域
GRANT SELECT ON ALL TABLES IN SCHEMA finance TO agent_readonly;
-- ai 域
GRANT SELECT ON ALL TABLES IN SCHEMA ai TO agent_readonly;

-- ═══ 5. 未来新建表自动继承（default privileges）═══
ALTER DEFAULT PRIVILEGES IN SCHEMA product GRANT SELECT ON TABLES TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA "order" GRANT SELECT ON TABLES TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA inventory GRANT SELECT ON TABLES TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT SELECT ON TABLES TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA crawler GRANT SELECT ON TABLES TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA finance GRANT SELECT ON TABLES TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO agent_readonly;

-- ═══ 6. 连接限制 ═══
ALTER ROLE agent_readonly SET statement_timeout = '30s';
ALTER ROLE agent_readonly SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE agent_readonly CONNECTION LIMIT 20;

-- 验证:
-- SELECT rolname, rolcanlogin, rolsuper FROM pg_roles WHERE rolname = 'agent_readonly';
-- \c agent_business agent_readonly
-- SELECT * FROM product.products LIMIT 1;  -- 应成功
-- DROP TABLE product.products;              -- 应失败: permission denied
