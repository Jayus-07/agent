-- =====================================================
-- 077_sql_query_audits_sql_text.sql
-- SQL 审计补存原文（2026-10-06 拍板：审计存 SQL 原文）
--
-- 目标库：agent_memory（同 042）
-- 口径变更：042 原「不存原文」系 PII 保守设计（生成 SQL 的 WHERE 可能
--   内嵌用户 literal）；2026-10-06 拍板改为存原文——事故可复盘优先，
--   PII 风险以「审计表仅管理员可读 + 保留期清理」缓解。
--   超长截断 8000 字符防行膨胀；query_hash 口径不变（聚合比对继续可用）。
-- 写入方：backend/sql/audit.py（best-effort，异步，失败不阻塞主查询）
-- =====================================================

ALTER TABLE sql_query_audits
    ADD COLUMN IF NOT EXISTS sql_text TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN sql_query_audits.sql_text IS
    '实际生成/执行的 SQL 原文（截断 8000 字符）；2026-10-06 拍板启用，此前行为见 042 头注释';
