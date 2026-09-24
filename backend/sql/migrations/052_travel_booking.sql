-- 052_travel_booking.sql（target: memory）
-- STOP L（Travel Booking Transaction & Order Lifecycle）：
--   travel schema 四表 + DB 级防并发/不可变守卫。
--   防并发重复靠唯一约束（booking_intent_id / merchant_order_id /
--   idempotency_key / (provider,event_id)），Python 检查只是快路径（§三十三）。
--   幂等权威账本仍是 ai.idempotency_records（047），本迁移不复制它。

CREATE SCHEMA IF NOT EXISTS travel;

-- ── 1. Booking Quote：不可变事实快照（任务书 §八/§九/§十）──────────
CREATE TABLE IF NOT EXISTS travel.booking_quotes (
    id                   BIGSERIAL PRIMARY KEY,
    quote_id             VARCHAR(64)  NOT NULL UNIQUE,
    tenant_id            VARCHAR(64)  NOT NULL,
    user_id              VARCHAR(64)  NOT NULL,

    commerce_type        VARCHAR(16)  NOT NULL,   -- hotel | flight
    provider             VARCHAR(64)  NOT NULL,   -- fake:commerce | ...
    provider_offer_id    VARCHAR(128),
    offer_fingerprint    VARCHAR(80)  NOT NULL,   -- STOP K offer 指纹

    booking_facts        JSONB        NOT NULL,   -- check_in/check_out/nights/occupancy 或航段要素

    price_amount         NUMERIC(18, 4) NOT NULL CHECK (price_amount > 0),
    currency             CHAR(3)      NOT NULL,
    taxes                NUMERIC(18, 4),
    fees                 NUMERIC(18, 4),
    tax_inclusion        VARCHAR(16)  NOT NULL DEFAULT 'unknown',

    availability         VARCHAR(16)  NOT NULL,   -- STOP K Availability 四态
    provider_observed_at TIMESTAMPTZ  NOT NULL,
    quote_created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    provider_expires_at  TIMESTAMPTZ,             -- 仅 Provider 明示（§十：禁伪造 guarantee）
    internal_expires_at  TIMESTAMPTZ  NOT NULL,   -- 系统 confirmation TTL（≠ 供应商锁价）
    quote_fingerprint    VARCHAR(80)  NOT NULL,   -- sha256（tenant+offer+facts+amount+currency+dates+occupancy）

    status               VARCHAR(16)  NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'expired', 'superseded', 'invalidated')),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_booking_quotes_tenant_user
    ON travel.booking_quotes (tenant_id, user_id, created_at DESC);

-- 不可变守卫（§八：价格变化 = 旧 Quote 失效 + 新 Quote，禁止原地改价）。
-- DB 级 trigger 兜底：UPDATE 命中事实列时，若金额/币种/指纹任一被改 → 拒绝。
CREATE OR REPLACE FUNCTION travel.fn_booking_quote_immutable() RETURNS trigger AS $$
BEGIN
    IF (NEW.price_amount, NEW.currency, NEW.quote_fingerprint, NEW.offer_fingerprint)
       IS DISTINCT FROM
       (OLD.price_amount, OLD.currency, OLD.quote_fingerprint, OLD.offer_fingerprint) THEN
        RAISE EXCEPTION 'booking quote % is immutable (price/fingerprint columns)',
            OLD.quote_id
            USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_booking_quote_immutable ON travel.booking_quotes;
CREATE TRIGGER trg_booking_quote_immutable
    BEFORE UPDATE ON travel.booking_quotes
    FOR EACH ROW EXECUTE FUNCTION travel.fn_booking_quote_immutable();

-- ── 2. Booking Order：订单状态机持久层（§三十二/§三十三）──────────
CREATE TABLE IF NOT EXISTS travel.booking_orders (
    id                   BIGSERIAL PRIMARY KEY,
    order_id             VARCHAR(64)  NOT NULL UNIQUE,
    tenant_id            VARCHAR(64)  NOT NULL,
    user_id              VARCHAR(64)  NOT NULL,

    booking_intent_id    VARCHAR(64)  NOT NULL UNIQUE,
    quote_id             VARCHAR(64)  NOT NULL,
    confirmation_id      VARCHAR(64)  NOT NULL,   -- 确认 gate 绑定快照指纹（§十一）

    commerce_type        VARCHAR(16)  NOT NULL,
    provider             VARCHAR(64)  NOT NULL,   -- booking provider（fake_booking_native 等）

    merchant_order_id    VARCHAR(80)  NOT NULL UNIQUE,
    provider_order_id    VARCHAR(128),            -- 外部引用（provider 回执/对账键）

    idempotency_key      VARCHAR(80)  NOT NULL UNIQUE,  -- derive_provider_key 派生

    amount               NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
    currency             CHAR(3)      NOT NULL,

    status               VARCHAR(24)  NOT NULL DEFAULT 'awaiting_confirmation'
                         CHECK (status IN ('quoted', 'awaiting_confirmation', 'confirmed',
                                           'submitting', 'booked', 'failed', 'in_doubt',
                                           'expired')),
    status_version       INTEGER      NOT NULL DEFAULT 0,  -- 单调递增（§二十）
    failure_code         VARCHAR(64),
    failure_class        VARCHAR(32),             -- rejected | not_sent | unknown | business

    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    submitted_at         TIMESTAMPTZ,
    booked_at            TIMESTAMPTZ,
    failed_at            TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_booking_orders_tenant_user
    ON travel.booking_orders (tenant_id, user_id, created_at DESC);
-- 恢复/对账扫描：stale submitting + in_doubt（§二十六）
CREATE INDEX IF NOT EXISTS idx_booking_orders_recovery
    ON travel.booking_orders (status, updated_at)
    WHERE status IN ('submitting', 'in_doubt', 'awaiting_confirmation');

-- ── 3. Webhook Inbox（§二十七/§二十九：先收进 inbox，不直接改单）──
CREATE TABLE IF NOT EXISTS travel.booking_webhook_inbox (
    id                   BIGSERIAL PRIMARY KEY,
    provider             VARCHAR(64)  NOT NULL,
    event_id             VARCHAR(128) NOT NULL,
    external_order_id    VARCHAR(128),
    event_type           VARCHAR(64)  NOT NULL,
    payload              JSONB        NOT NULL,
    payload_hash         VARCHAR(80)  NOT NULL,
    received_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at         TIMESTAMPTZ,
    status               VARCHAR(16)  NOT NULL DEFAULT 'received'
                         CHECK (status IN ('received', 'processed', 'quarantined', 'ignored')),
    quarantine_reason    VARCHAR(128)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_webhook_event
    ON travel.booking_webhook_inbox (provider, event_id);

-- ── 4. Booking Events：append-only 审计/事件账（§四十一；CS outbox
--    同模式：与状态转换同事务追加 + event_id 去重；relay 接入留待需要）──
CREATE TABLE IF NOT EXISTS travel.booking_events (
    id                   BIGSERIAL PRIMARY KEY,
    event_id             VARCHAR(64)  NOT NULL UNIQUE,
    tenant_id            VARCHAR(64)  NOT NULL,
    order_id             VARCHAR(64),             -- 订单前置事件（quote_created）可空
    quote_id             VARCHAR(64),
    event_type           VARCHAR(48)  NOT NULL,   -- quote_created / booking_submit_started / ...
    actor                VARCHAR(64)  NOT NULL DEFAULT '',
    result               VARCHAR(24)  NOT NULL DEFAULT '',
    detail               JSONB,                   -- 低基数摘要；禁 secret/PII/raw payload
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_booking_events_order
    ON travel.booking_events (order_id, id);
