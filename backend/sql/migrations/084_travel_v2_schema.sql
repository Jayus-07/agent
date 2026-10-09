-- 旅游助手 V2 正式行程存储；位于独立 schema，不触碰 travel 预订数据。
CREATE SCHEMA IF NOT EXISTS travel_v2;

CREATE TABLE travel_v2.templates (
    template_id UUID PRIMARY KEY,
    slug VARCHAR(160) NOT NULL UNIQUE,
    title VARCHAR(200) NOT NULL,
    destination VARCHAR(160) NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    cover_image_url TEXT,
    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    status VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'archived')),
    sort_weight INTEGER NOT NULL DEFAULT 0,
    content JSONB NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_template_content_v2 CHECK (
        jsonb_typeof(content) = 'object'
        AND content ? 'schema_version'
        AND content->'schema_version' = '2'::jsonb
        AND content ? 'brief'
        AND jsonb_typeof(content->'brief') = 'object'
        AND content ? 'days'
        AND jsonb_typeof(content->'days') = 'array'
        AND content ? 'selections'
        AND jsonb_typeof(content->'selections') = 'array'
        AND content ? 'totals'
        AND jsonb_typeof(content->'totals') = 'object'
        AND content ? 'health'
        AND jsonb_typeof(content->'health') = 'object'
    ),
    CONSTRAINT chk_template_published_at CHECK (
        status <> 'published' OR published_at IS NOT NULL
    )
);

CREATE INDEX idx_travel_v2_templates_published
    ON travel_v2.templates (destination, sort_weight DESC, published_at DESC)
    WHERE status = 'published';

CREATE TABLE travel_v2.trips (
    trip_id UUID PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    title VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    document JSONB NOT NULL,
    source_template_id UUID
        REFERENCES travel_v2.templates(template_id) ON DELETE RESTRICT,
    source_template_version BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_trip_tenant_nonempty CHECK (length(btrim(tenant_id)) > 0),
    CONSTRAINT chk_trip_owner_nonempty CHECK (length(btrim(owner_id)) > 0),
    CONSTRAINT chk_trip_document_v2 CHECK (
        jsonb_typeof(document) = 'object'
        AND document ? 'schema_version'
        AND document->'schema_version' = '2'::jsonb
        AND document ? 'brief'
        AND jsonb_typeof(document->'brief') = 'object'
        AND document ? 'days'
        AND jsonb_typeof(document->'days') = 'array'
        AND document ? 'selections'
        AND jsonb_typeof(document->'selections') = 'array'
        AND document ? 'totals'
        AND jsonb_typeof(document->'totals') = 'object'
        AND document ? 'health'
        AND jsonb_typeof(document->'health') = 'object'
    ),
    CONSTRAINT chk_trip_template_source CHECK (
        (source_template_id IS NULL AND source_template_version IS NULL)
        OR (
            source_template_id IS NOT NULL
            AND source_template_version IS NOT NULL
            AND source_template_version >= 1
        )
    ),
    CONSTRAINT uq_travel_v2_trip_scope UNIQUE (trip_id, tenant_id, owner_id)
);

CREATE INDEX idx_travel_v2_trips_owner_recent
    ON travel_v2.trips (tenant_id, owner_id, updated_at DESC)
    WHERE status = 'active';
CREATE INDEX idx_travel_v2_trips_destination
    ON travel_v2.trips (tenant_id, (document #>> '{brief,destination}'))
    WHERE status = 'active';

CREATE TABLE travel_v2.trip_revisions (
    trip_id UUID NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    parent_revision BIGINT,
    snapshot JSONB NOT NULL,
    change_type VARCHAR(48) NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    actor_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_travel_v2_trip_revisions PRIMARY KEY (trip_id, revision),
    CONSTRAINT fk_travel_v2_revision_owner
        FOREIGN KEY (trip_id, tenant_id, owner_id)
        REFERENCES travel_v2.trips (trip_id, tenant_id, owner_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_travel_v2_revision_parent
        FOREIGN KEY (trip_id, parent_revision)
        REFERENCES travel_v2.trip_revisions (trip_id, revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT chk_travel_v2_revision_chain CHECK (
        (revision = 1 AND parent_revision IS NULL)
        OR (revision > 1 AND parent_revision = revision - 1)
    ),
    CONSTRAINT chk_travel_v2_revision_snapshot CHECK (
        jsonb_typeof(snapshot) = 'object'
        AND snapshot ? 'schema_version'
        AND snapshot->'schema_version' = '2'::jsonb
        AND snapshot ? 'days'
        AND jsonb_typeof(snapshot->'days') = 'array'
    )
);

CREATE INDEX idx_travel_v2_revisions_owner_recent
    ON travel_v2.trip_revisions (tenant_id, owner_id, created_at DESC);

CREATE TABLE travel_v2.edit_operations (
    operation_id UUID PRIMARY KEY,
    trip_id UUID NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_sha256 CHAR(64) NOT NULL,
    command_type VARCHAR(48) NOT NULL,
    base_revision BIGINT NOT NULL CHECK (base_revision >= 1),
    result_revision BIGINT NOT NULL,
    response JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_travel_v2_edit_idempotency UNIQUE (trip_id, idempotency_key),
    CONSTRAINT fk_travel_v2_edit_owner
        FOREIGN KEY (trip_id, tenant_id, owner_id)
        REFERENCES travel_v2.trips (trip_id, tenant_id, owner_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_travel_v2_edit_revision
        FOREIGN KEY (trip_id, result_revision)
        REFERENCES travel_v2.trip_revisions (trip_id, revision)
        ON DELETE RESTRICT,
    CONSTRAINT chk_travel_v2_edit_revision
        CHECK (result_revision = base_revision + 1),
    CONSTRAINT chk_travel_v2_edit_response
        CHECK (jsonb_typeof(response) = 'object'),
    CONSTRAINT chk_travel_v2_edit_key
        CHECK (length(btrim(idempotency_key)) > 0),
    CONSTRAINT chk_travel_v2_edit_hash
        CHECK (request_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_travel_v2_edit_operations_recent
    ON travel_v2.edit_operations (tenant_id, owner_id, trip_id, created_at DESC);
