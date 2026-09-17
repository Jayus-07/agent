-- =====================================================
-- 016_inventory_alerts_pg.sql — 库存告警 4 表（迁移计划 Batch C）
-- 目标库：agent_business（业务属性，对 NL2SQL 可见）
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行
-- 对应实现：backend/orchestration/inventory/store_pg.py
-- 说明：SQLite 版 INTEGER AUTOINCREMENT → BIGSERIAL；
--       SQLite 版 INTEGER 布尔列 → BOOLEAN
-- =====================================================

-- 阈值规则（决策 1）
CREATE TABLE IF NOT EXISTS inventory_threshold_rules (
    id                  BIGSERIAL PRIMARY KEY,
    rule_type           TEXT NOT NULL,           -- sku / category / global
    product_id          TEXT,                    -- sku 规则专用
    category            TEXT,                    -- category 规则专用

    min_qty             INTEGER NOT NULL,
    days_of_stock       INTEGER DEFAULT 7,
    sales_window_days   INTEGER DEFAULT 30,
    alert_level         TEXT DEFAULT 'warning',  -- info/warning/critical
    enabled             BOOLEAN DEFAULT true,

    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_thresholds_match
    ON inventory_threshold_rules(rule_type, product_id, category, enabled);

-- 告警 case（决策 2.5 当前工单）
CREATE TABLE IF NOT EXISTS inventory_alert_cases (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          TEXT NOT NULL UNIQUE,    -- 一件商品永远一个 case
    current_state       TEXT,                    -- low/critical/out_of_stock
    current_level       TEXT,                    -- info/warning/critical
    status              TEXT,                    -- open/acknowledged/resolved/closed
    resolution_type     TEXT,                    -- AUTO_RECOVERED / MANUAL_RESOLVED / MANUAL_IGNORED

    first_detected_at   TEXT,
    last_detected_at    TEXT,
    last_notified_at    TEXT,

    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_status
    ON inventory_alert_cases(status, product_id);

-- 事件溯源（决策 2.5）
CREATE TABLE IF NOT EXISTS inventory_alert_events (
    id              BIGSERIAL PRIMARY KEY,
    case_id         BIGINT NOT NULL,
    event_type      TEXT NOT NULL,    -- created/upgraded/reminded/resolved/reopened
    from_state      TEXT,
    to_state        TEXT,
    qty             INTEGER,
    stock_days      DOUBLE PRECISION,
    reason          TEXT,            -- JSON
    notified        BOOLEAN DEFAULT false,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_case
    ON inventory_alert_events(case_id, created_at);

-- 通知策略（决策 3）
CREATE TABLE IF NOT EXISTS notification_policies (
    id                  BIGSERIAL PRIMARY KEY,
    policy_name         TEXT NOT NULL,
    alert_level         TEXT,            -- NULL = 全部
    inventory_state     TEXT,            -- NULL = 全部
    category            TEXT,            -- NULL = 全部
    notify_email        TEXT,            -- 多个用 ; 分隔
    notify_on_upgrade   BOOLEAN DEFAULT true,
    notify_on_remind    BOOLEAN DEFAULT true,
    notify_on_resolve   BOOLEAN DEFAULT true,
    enabled             BOOLEAN DEFAULT true,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_policies_match
    ON notification_policies(enabled, alert_level, inventory_state);
