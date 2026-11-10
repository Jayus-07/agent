-- ═══════════════════════════════════════════════════════════════════
-- 147_readonly_revoke_cs_business_tables.sql — 回收 CS 能力批次新表的只读误授权
--
-- 目标库：agent_business
--
-- 背景（周回归 v3 run 42601291303 parity 守卫实证）：
--   004_readonly_role.sql 的 schema 级 GRANT（SELECT ON ALL TABLES IN
--   SCHEMA customer/finance/...）让 CS 能力批次新建的业务表自动获得
--   agent_readonly SELECT：
--     - 132_cs_points.sql   → finance.points_ledger / customer.tier_profiles
--     - 134_cs_payment.sql  → finance.wallet_balances / finance.wallet_ledger
--     - 138_cs_invoice_business.sql → customer.invoice_titles / finance.invoices
--   这 6 张表均不在 SQL Agent 纳管清单
--   （backend/sql/data/schema_config.py::SCHEMA_CONFIG["tables"]），
--   validator Layer 2 本就拒绝访问；CS 意图服务（积分/发票/钱包）走
--   get_business_engine（BUSINESS_DB_CONFIG，PGUSER），不经只读池。
--   授权没有任何被消费的路径，纵深防御少一层——083/043 同口径回收。
--
-- 口径：**只读角色的可见面恒等于 SQL Agent 声明面**。
--   派生源 = schema_config.SCHEMA_CONFIG["tables"]；
--   守卫 backend/tests/sql/test_schema_config_db_parity.py 持续校验。
--
-- 幂等：REVOKE 可重复执行。
-- ═══════════════════════════════════════════════════════════════════

BEGIN;

-- CS 积分/等级（132）
REVOKE SELECT ON TABLE finance.points_ledger FROM agent_readonly;
REVOKE SELECT ON TABLE customer.tier_profiles FROM agent_readonly;

-- CS 钱包（125_mall_wallet；CI workflow 此前仅在此处单独 REVOKE）
REVOKE SELECT ON TABLE finance.wallet_balances FROM agent_readonly;
REVOKE SELECT ON TABLE finance.wallet_ledger FROM agent_readonly;

-- CS 发票（138）
REVOKE SELECT ON TABLE customer.invoice_titles FROM agent_readonly;
REVOKE SELECT ON TABLE finance.invoices FROM agent_readonly;

-- 默认权限兜底（评审链路复查 2026-11-10 补，083 同思路）：
-- 004 第 58-63 行的 ALTER DEFAULT PRIVILEGES 会把今后 customer/finance
-- 新建表自动授给只读角色——132/134/138 批次的误授权正源于此。上面只
-- 回收存量，不堵增量则下批新表复发（parity 守卫会再次红）。按现行
-- 口径「新业务表默认不纳管（fail-closed）」，今后确需只读访问时在
-- 083 口径显式 GRANT。
ALTER DEFAULT PRIVILEGES IN SCHEMA customer REVOKE ALL ON TABLES FROM agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA finance REVOKE ALL ON TABLES FROM agent_readonly;

COMMIT;
