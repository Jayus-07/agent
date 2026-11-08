-- 140_selection_funnel_tenant.sql — 选品漏斗三表补租户维度（2026-11-08 安全审计 T4d）
--
-- 背景：docs/domains/selection-ecommerce.md P1-8 记录 import_candidates /
-- keyword_stats / product_reviews 无 tenant 字段，导入、列表与按 batch 删除
-- 都是全局作用。本迁移只补 Schema 与索引；读写过滤由
-- backend/selection_funnel/import_pool_pg.py / market_data_pg.py 同步落地。
--
-- 存量行归属 'default'（与全仓历史行缺省语义一致）；新写入必须显式带租户。
-- 幂等：ADD COLUMN IF NOT EXISTS，可在已有库重放。

ALTER TABLE import_candidates
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE keyword_stats
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE product_reviews
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_sf_imp_tenant_batch
    ON import_candidates(tenant_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_sf_imp_tenant_url
    ON import_candidates(tenant_id, url);
CREATE INDEX IF NOT EXISTS idx_sf_kw_tenant_cat
    ON keyword_stats(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_sf_rv_tenant_cat
    ON product_reviews(tenant_id, category);
