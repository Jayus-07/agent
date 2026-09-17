-- 021_selection_funnel_pg.sql — 选品漏斗导入存储 PG 化（2026-09-18，高并发设计）
--
-- 三张表：import_candidates（商品榜导入池）/ keyword_stats（关键词榜）/
-- product_reviews（差评实证）。库归属 agent_business（业务族，对 NL2SQL 可见）。
-- 代码侧 schema：backend/selection_funnel/import_pool_pg.py / market_data_pg.py
-- （工厂默认 postgres；SELECTION_FUNNEL_DB_BACKEND=sqlite 为测试逃生舱）。
--
-- 高并发要点：
--   1. 同款去重在 build_pool 层统一负责（保留最新批次，旧行进 reasons 披露），
--      数据层保持哑管道；数据层与建池层职责单一
--   2. 批量写入 execute_values 单事务（代码侧）
--   3. 连接池 ThreadedConnectionPool（代码侧，min/max 读 config DB_POOL_*）
-- 时间戳语义：应用侧生成 ISO 文本写入，不依赖 PG 服务器时区（全仓约定）。

-- ── 商品榜导入池 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS import_candidates (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    title TEXT NOT NULL,
    platform TEXT DEFAULT '',
    price DOUBLE PRECISION,
    original_price DOUBLE PRECISION,
    rating DOUBLE PRECISION,
    review_count INTEGER,
    sales INTEGER,
    unit_cost DOUBLE PRECISION,
    category TEXT DEFAULT '',
    url TEXT DEFAULT '',
    promo_text TEXT DEFAULT '',
    highlights TEXT DEFAULT '',
    extra_json TEXT DEFAULT '',
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sf_imp_category ON import_candidates(category);
CREATE INDEX IF NOT EXISTS idx_sf_imp_batch ON import_candidates(batch_id);
-- 同款历史趋势批量取数（verifier → history_by_keys）按 url 定位
CREATE INDEX IF NOT EXISTS idx_sf_imp_url ON import_candidates(url);

-- ── 关键词榜（赛道画像：top 词 / 机会词）─────────────────────
CREATE TABLE IF NOT EXISTS keyword_stats (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    category TEXT DEFAULT '',
    keyword TEXT,
    search_pop DOUBLE PRECISION,
    click_rate DOUBLE PRECISION,
    pay_rate DOUBLE PRECISION,
    competition DOUBLE PRECISION,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sf_kw_cat ON keyword_stats(category);
CREATE INDEX IF NOT EXISTS idx_sf_kw_pop ON keyword_stats(search_pop DESC);

-- ── 差评实证（痛点机会：规则桶聚类）─────────────────────────
CREATE TABLE IF NOT EXISTS product_reviews (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    category TEXT DEFAULT '',
    product_title TEXT,
    content TEXT,
    star DOUBLE PRECISION,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sf_rv_cat ON product_reviews(category);
