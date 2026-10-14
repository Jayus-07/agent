-- 持久化 Trip 绑定 AI 请求的原始指纹与终态，确保模型调用前同键单占位。
CREATE TABLE travel_v2.ai_edit_requests (
    request_id UUID PRIMARY KEY,
    trip_id UUID NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_sha256 CHAR(64) NOT NULL,
    expected_revision BIGINT NOT NULL CHECK (expected_revision >= 1),
    status VARCHAR(16) NOT NULL CHECK (
        status IN ('processing', 'succeeded', 'read_only', 'clarify',
                   'unsupported', 'failed', 'unknown')
    ),
    response JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(response) = 'object'),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_travel_v2_ai_edit_idempotency UNIQUE (trip_id, idempotency_key),
    CONSTRAINT fk_travel_v2_ai_edit_owner
        FOREIGN KEY (trip_id, tenant_id, owner_id)
        REFERENCES travel_v2.trips (trip_id, tenant_id, owner_id)
        ON DELETE RESTRICT,
    CONSTRAINT chk_travel_v2_ai_edit_key
        CHECK (length(btrim(idempotency_key)) > 0),
    CONSTRAINT chk_travel_v2_ai_edit_hash
        CHECK (request_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_travel_v2_ai_edit_requests_recent
    ON travel_v2.ai_edit_requests (tenant_id, owner_id, trip_id, created_at DESC);
