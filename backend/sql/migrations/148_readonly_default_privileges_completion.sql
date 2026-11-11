-- ═══════════════════════════════════════════════════════════════════
-- 148_readonly_default_privileges_completion.sql — 只读角色默认授权收官
--
-- 目标库：agent_business
--
-- 背景（issue #440，周回归 parity 守卫实证链的收尾）：
--   「agent_readonly 的可见面恒等于 SQL Agent 声明面」口径由 043（public）
--   / 083（ai）/ 147（customer+finance 存量与增量）逐步确立；本迁移补齐
--   004 第 58-65 行仍对 product / "order" / inventory / crawler 保留的
--   ALTER DEFAULT PRIVILEGES——此前这四个 schema 的新建表会自动获得
--   只读 SELECT，不在纳管清单即属越权授权（132/134/138 六表即此路径
--   产生，147 已回收存量）。
--
-- 口径（083 既定）：新业务表默认不纳管（fail-closed）；确需只读访问时
--   在建表迁移内按 083 口径显式 GRANT（parity 守卫
--   test_schema_config_db_parity 双向锁死：多一张=越权、少一张=声明读不到）。
--
-- 幂等：ALTER DEFAULT PRIVILEGES 可重复执行。
-- ═══════════════════════════════════════════════════════════════════

BEGIN;

ALTER DEFAULT PRIVILEGES IN SCHEMA product REVOKE ALL ON TABLES FROM agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA "order" REVOKE ALL ON TABLES FROM agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA inventory REVOKE ALL ON TABLES FROM agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA crawler REVOKE ALL ON TABLES FROM agent_readonly;

COMMIT;
