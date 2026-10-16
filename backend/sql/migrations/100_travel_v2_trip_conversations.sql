-- 服务端保存正式 Trip 与其原始规划会话的不可变关联。
CREATE TABLE travel_v2.trip_conversations (
    trip_id UUID PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    conversation_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_travel_v2_trip_conversation_owner
        UNIQUE (tenant_id, owner_id, conversation_id),
    CONSTRAINT fk_travel_v2_trip_conversation_owner
        FOREIGN KEY (trip_id, tenant_id, owner_id)
        REFERENCES travel_v2.trips (trip_id, tenant_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_travel_v2_trip_conversation_nonempty
        CHECK (length(btrim(conversation_id)) BETWEEN 1 AND 128)
);
