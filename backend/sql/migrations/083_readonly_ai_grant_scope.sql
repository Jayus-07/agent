-- ═══════════════════════════════════════════════════════════════════
-- 083_readonly_ai_grant_scope.sql — agent_readonly 的 ai 域授权收窄为声明清单
--
-- 目标库：agent_business
--
-- 背景（2026-10-08 实测）：
--   004_readonly_role.sql 用 `GRANT SELECT ON ALL TABLES IN SCHEMA ai`
--   把整个 ai schema 授予只读角色，并配 `ALTER DEFAULT PRIVILEGES` 让后建表
--   自动继承；于是 007_tool_approval.sql 建出的 ai.tool_approval_requests
--   也拿到了 SELECT——而 SQL Agent 的纳管清单
--   （backend/sql/data/schema_config.py::SCHEMA_CONFIG["tables"]）里 ai 域
--   只有 agent_tasks / agent_trace 两张表。后果是这条授权没有任何被消费的
--   路径（只读池只服务 SQL 执行器，该表被 validator Layer 2 拒），
--   表级防线实际只剩校验器一层，纵深防御少一层。
--   043_readonly_public_revoke.sql 已按同一思路收干净 public schema，本迁移补齐 ai。
--
-- 口径：**只读角色的可见面恒等于 SQL Agent 声明面**。
--   派生源 = schema_config.SCHEMA_CONFIG["tables"]（G2：不手抄第二份清单）；
--   守卫测试 backend/tests/sql/test_schema_config_db_parity.py 持续校验二者一致。
--
-- 幂等：REVOKE / GRANT 可重复执行。
-- ═══════════════════════════════════════════════════════════════════

BEGIN;

-- 1. 回收 ai 域全量表/序列授权（含 007 建出的 ai.tool_approval_requests）
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA ai FROM agent_readonly;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA ai FROM agent_readonly;

-- 2. 默认权限兜底：今后 ai 域新建表不再自动授予只读角色
--    （new table = 新纳管面，必须显式声明 + 显式授权，不给"默认可见"）
ALTER DEFAULT PRIVILEGES IN SCHEMA ai REVOKE ALL ON TABLES FROM agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai REVOKE ALL ON SEQUENCES FROM agent_readonly;

-- 3. 只对声明纳管的两张表重新授予（schema USAGE 保留，否则连表名都解析不到）
GRANT USAGE ON SCHEMA ai TO agent_readonly;
GRANT SELECT ON ai.agent_tasks, ai.agent_trace TO agent_readonly;

COMMIT;

-- 核对（期望恰好 2 行：ai.agent_tasks / ai.agent_trace）：
--   SELECT table_schema||'.'||table_name
--     FROM information_schema.table_privileges
--    WHERE grantee = 'agent_readonly' AND privilege_type = 'SELECT'
--      AND table_schema = 'ai'
--    ORDER BY 1;
