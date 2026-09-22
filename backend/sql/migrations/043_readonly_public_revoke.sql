-- =====================================================
-- 043_readonly_public_revoke.sql
-- SQL Agent 生产收口（STOP C §十三）：数据库层最小权限收口
--
-- 背景：004_readonly_role.sql 曾对 agent_business 全部 schema（含 public）
--   执行 GRANT SELECT ON ALL TABLES——public schema 的 20 张应用自有表
--   （selection/feedback/competitor_watch 等）不在 SQL Agent 白名单内，
--   形成「AST 白名单拒绝、DB 层却放行」的纵深缺口（STOP A 审计 G10）。
-- 目标：agent_readonly 只保留 7 个业务 schema 的 SELECT，public 全部回收。
-- 不影响：postgres（owner/migration 用户）与其他业务写账号。
--
-- 幂等：REVOKE 天然幂等，可重复执行。
-- =====================================================

-- 1. 回收 public schema 全部表/序列的 SELECT（显式 grant 的部分）
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM agent_readonly;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM agent_readonly;

-- 2. 默认权限兜底：今后 public 新建表不再自动授予 agent_readonly
--    （仅当存在该 default privilege 时生效；无则无害）
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_default_acl d
        JOIN pg_namespace n ON n.oid = d.defaclnamespace
        WHERE n.nspname = 'public'
    ) THEN
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM agent_readonly;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM agent_readonly;
    END IF;
END
$$;
