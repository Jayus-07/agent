-- 142_cs_faq_tenant.sql — FAQ 按租户隔离（2026-11-09 安全审计）
--
-- 背景：ai.cs_faq 原来只有全局 faq_key UNIQUE，A 租户的 FAQ 会被 B 租户
-- 的问答命中，跨租户泄露答案。改为 (tenant_id, faq_key) 唯一，存量行归
-- 属 default（与其它表 tenant 回填口径一致）。
--
-- 回滚（down）：
--   DROP INDEX IF EXISTS ai.uq_cs_faq_tenant_faq_key;
--   DROP INDEX IF EXISTS ai.idx_cs_faq_tenant_status;
--   ALTER TABLE ai.cs_faq DROP COLUMN IF EXISTS tenant_id;
--   ALTER TABLE ai.cs_faq ADD CONSTRAINT cs_faq_faq_key_key UNIQUE (faq_key);
--   （回滚前须确认 faq_key 在全局仍唯一，否则先清理重复行）

ALTER TABLE ai.cs_faq
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

-- 076 建表时的全局唯一约束（Postgres 默认名）
ALTER TABLE ai.cs_faq DROP CONSTRAINT IF EXISTS cs_faq_faq_key_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_faq_tenant_faq_key
    ON ai.cs_faq (tenant_id, faq_key);

CREATE INDEX IF NOT EXISTS idx_cs_faq_tenant_status
    ON ai.cs_faq (tenant_id, status);
