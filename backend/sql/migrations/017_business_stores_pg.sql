-- =====================================================
-- 017_business_stores_pg.sql — 业务族 5 store 共 11 表（迁移计划 Batch D）
-- 目标库：agent_business（业务数据，对 NL2SQL 可见；agent_readonly 已挂该库）
-- 幂等：CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE FUNCTION，可重复执行
-- 对应实现：
--   selection          → backend/selection/store_pg.py
--   selection_decision → backend/selection_decision/store_pg.py
--   market_research    → backend/market_research/store_pg.py
--   competitor         → backend/competitor/store_pg.py
--   feedback           → backend/feedback/pg.py
-- =====================================================

-- ──────────── selection（评分缓存 + 权重配置） ────────────

CREATE TABLE IF NOT EXISTS selection_scores (
    url         TEXT PRIMARY KEY,
    score_json  TEXT NOT NULL,
    snapshot_id BIGINT,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS selection_weights (
    key        TEXT PRIMARY KEY,
    value      DOUBLE PRECISION NOT NULL,
    updated_at TEXT NOT NULL
);

-- ──────────── selection_decision（任务 + 决策留痕） ────────────

CREATE TABLE IF NOT EXISTS selection_tasks (
    id           TEXT PRIMARY KEY,
    inputs_json  TEXT NOT NULL,
    status       TEXT NOT NULL,
    verdict      TEXT,
    report_md    TEXT,
    trace_id     TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sd_tasks_created ON selection_tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS decision_log (
    decision_id       TEXT PRIMARY KEY,
    task_id           TEXT,
    candidate_id      TEXT NOT NULL,
    category          TEXT,
    decision_version  INTEGER NOT NULL DEFAULT 1,
    evidence_snapshot TEXT NOT NULL,
    score_snapshot    TEXT NOT NULL,
    recommendation    TEXT NOT NULL,
    user_decision     TEXT,
    decision_at       TEXT NOT NULL,
    actual_metrics    TEXT,
    feedback_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_dlog_candidate ON decision_log(candidate_id, decision_version DESC);

-- 快照不可变兜底（与 SQLite 版触发器语义一致）：
-- evidence_snapshot / score_snapshot 写入后禁止 UPDATE，改权重/重抓数据一律新增 decision_version 行
CREATE OR REPLACE FUNCTION trg_decision_log_snapshot_immutable_fn()
RETURNS trigger AS $$
BEGIN
    IF OLD.evidence_snapshot IS DISTINCT FROM NEW.evidence_snapshot
       OR OLD.score_snapshot IS DISTINCT FROM NEW.score_snapshot THEN
        RAISE EXCEPTION 'decision_log 快照不可变：evidence_snapshot/score_snapshot 禁止更新，请新增 decision_version 行';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_decision_log_snapshot_immutable ON decision_log;
CREATE TRIGGER trg_decision_log_snapshot_immutable
BEFORE UPDATE ON decision_log
FOR EACH ROW EXECUTE FUNCTION trg_decision_log_snapshot_immutable_fn();

-- ──────────── market_research（任务 + 证据） ────────────

CREATE TABLE IF NOT EXISTS mr_tasks (
    id           TEXT PRIMARY KEY,
    inputs_json  TEXT NOT NULL,
    status       TEXT NOT NULL,
    report_md    TEXT,
    trace_id     TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mr_tasks_created ON mr_tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS mr_evidence (
    task_id       TEXT NOT NULL,
    evidence_id   TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    source_type   TEXT,
    raw_text      TEXT,
    numbers_json  TEXT,
    search_query  TEXT,
    fetched_at    TEXT,
    published_at  TEXT,
    PRIMARY KEY (task_id, evidence_id)
);

-- ──────────── competitor（监控项 + 快照 + 配置 + 事件） ────────────

CREATE TABLE IF NOT EXISTS competitor_watchlist (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    platform    TEXT DEFAULT 'generic',
    my_sku      TEXT DEFAULT '',
    frequency   TEXT DEFAULT 'daily',
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    watchlist_id   BIGINT,
    url            TEXT NOT NULL,
    platform       TEXT DEFAULT 'generic',
    title          TEXT DEFAULT '',
    price          DOUBLE PRECISION,
    original_price DOUBLE PRECISION,
    currency       TEXT DEFAULT 'CNY',
    promo_text     TEXT DEFAULT '',
    rating         DOUBLE PRECISION,
    review_count   BIGINT,
    in_stock       INTEGER DEFAULT 1,
    highlights     TEXT DEFAULT '',
    extract_method TEXT DEFAULT 'llm',
    raw_excerpt    TEXT DEFAULT '',
    crawled_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_url_time
    ON competitor_snapshots(url, crawled_at DESC);

CREATE TABLE IF NOT EXISTS competitor_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitor_events (
    id         BIGSERIAL PRIMARY KEY,
    platform   TEXT DEFAULT '',
    url        TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    detail     TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON competitor_events(created_at DESC);

-- ──────────── feedback（反馈投票） ────────────

CREATE TABLE IF NOT EXISTS feedback (
    id             BIGSERIAL PRIMARY KEY,
    session_id     TEXT NOT NULL,
    msg_id         TEXT,
    question       TEXT,
    answer_preview TEXT,
    vote           TEXT NOT NULL CHECK (vote IN ('positive', 'negative')),
    reason         TEXT,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_vote ON feedback(vote);
