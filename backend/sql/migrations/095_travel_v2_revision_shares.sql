-- 固定 Revision 的只读分享链接；不复制或建立第二套 Trip/Revision 数据模型。
CREATE TABLE travel_v2.trip_shares (
    share_id UUID PRIMARY KEY,
    trip_id UUID NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    token_sha256 CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_travel_v2_share_owner
        FOREIGN KEY (trip_id, tenant_id, owner_id)
        REFERENCES travel_v2.trips (trip_id, tenant_id, owner_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_travel_v2_share_revision
        FOREIGN KEY (trip_id, revision)
        REFERENCES travel_v2.trip_revisions (trip_id, revision)
        ON DELETE RESTRICT,
    CONSTRAINT chk_travel_v2_share_token_hash
        CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_travel_v2_share_expiry
        CHECK (expires_at > created_at),
    CONSTRAINT chk_travel_v2_share_revocation
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX idx_travel_v2_shares_owner_recent
    ON travel_v2.trip_shares (tenant_id, owner_id, trip_id, created_at DESC);
