-- Extend the applied V2 JSONB contract without editing migration 084.
-- Existing documents are preserved and receive empty arrangement collections.
UPDATE travel_v2.templates
SET content = jsonb_set(
    jsonb_set(
        jsonb_set(
            content,
            '{arrangements}',
            CASE WHEN jsonb_typeof(content->'arrangements') = 'object'
                THEN content->'arrangements' ELSE '{}'::jsonb END,
            true
        ),
        '{arrangements,lodgings}',
        CASE WHEN jsonb_typeof(content #> '{arrangements,lodgings}') = 'array'
            THEN content #> '{arrangements,lodgings}' ELSE '[]'::jsonb END,
        true
    ),
    '{arrangements,intercity_trains}',
    CASE WHEN jsonb_typeof(content #> '{arrangements,intercity_trains}') = 'array'
        THEN content #> '{arrangements,intercity_trains}' ELSE '[]'::jsonb END,
    true
)
WHERE jsonb_typeof(content->'arrangements') IS DISTINCT FROM 'object'
   OR jsonb_typeof(content #> '{arrangements,lodgings}') IS DISTINCT FROM 'array'
   OR jsonb_typeof(content #> '{arrangements,intercity_trains}') IS DISTINCT FROM 'array';

UPDATE travel_v2.trips
SET document = jsonb_set(
    jsonb_set(
        jsonb_set(
            document,
            '{arrangements}',
            CASE WHEN jsonb_typeof(document->'arrangements') = 'object'
                THEN document->'arrangements' ELSE '{}'::jsonb END,
            true
        ),
        '{arrangements,lodgings}',
        CASE WHEN jsonb_typeof(document #> '{arrangements,lodgings}') = 'array'
            THEN document #> '{arrangements,lodgings}' ELSE '[]'::jsonb END,
        true
    ),
    '{arrangements,intercity_trains}',
    CASE WHEN jsonb_typeof(document #> '{arrangements,intercity_trains}') = 'array'
        THEN document #> '{arrangements,intercity_trains}' ELSE '[]'::jsonb END,
    true
)
WHERE jsonb_typeof(document->'arrangements') IS DISTINCT FROM 'object'
   OR jsonb_typeof(document #> '{arrangements,lodgings}') IS DISTINCT FROM 'array'
   OR jsonb_typeof(document #> '{arrangements,intercity_trains}') IS DISTINCT FROM 'array';

UPDATE travel_v2.trip_revisions
SET snapshot = jsonb_set(
    jsonb_set(
        jsonb_set(
            snapshot,
            '{arrangements}',
            CASE WHEN jsonb_typeof(snapshot->'arrangements') = 'object'
                THEN snapshot->'arrangements' ELSE '{}'::jsonb END,
            true
        ),
        '{arrangements,lodgings}',
        CASE WHEN jsonb_typeof(snapshot #> '{arrangements,lodgings}') = 'array'
            THEN snapshot #> '{arrangements,lodgings}' ELSE '[]'::jsonb END,
        true
    ),
    '{arrangements,intercity_trains}',
    CASE WHEN jsonb_typeof(snapshot #> '{arrangements,intercity_trains}') = 'array'
        THEN snapshot #> '{arrangements,intercity_trains}' ELSE '[]'::jsonb END,
    true
)
WHERE jsonb_typeof(snapshot->'arrangements') IS DISTINCT FROM 'object'
   OR jsonb_typeof(snapshot #> '{arrangements,lodgings}') IS DISTINCT FROM 'array'
   OR jsonb_typeof(snapshot #> '{arrangements,intercity_trains}') IS DISTINCT FROM 'array';

ALTER TABLE travel_v2.templates
    DROP CONSTRAINT chk_template_content_v2;
ALTER TABLE travel_v2.templates
    ADD CONSTRAINT chk_template_content_v2 CHECK (
        jsonb_typeof(content) = 'object'
        AND content ? 'schema_version'
        AND content->'schema_version' = '2'::jsonb
        AND content ? 'brief'
        AND jsonb_typeof(content->'brief') = 'object'
        AND content ? 'days'
        AND jsonb_typeof(content->'days') = 'array'
        AND content ? 'selections'
        AND jsonb_typeof(content->'selections') = 'array'
        AND content ? 'arrangements'
        AND jsonb_typeof(content->'arrangements') = 'object'
        AND jsonb_typeof(content #> '{arrangements,lodgings}') = 'array'
        AND jsonb_typeof(content #> '{arrangements,intercity_trains}') = 'array'
        AND content ? 'totals'
        AND jsonb_typeof(content->'totals') = 'object'
        AND content ? 'health'
        AND jsonb_typeof(content->'health') = 'object'
    );

ALTER TABLE travel_v2.trips
    DROP CONSTRAINT chk_trip_document_v2;
ALTER TABLE travel_v2.trips
    ADD CONSTRAINT chk_trip_document_v2 CHECK (
        jsonb_typeof(document) = 'object'
        AND document ? 'schema_version'
        AND document->'schema_version' = '2'::jsonb
        AND document ? 'brief'
        AND jsonb_typeof(document->'brief') = 'object'
        AND document ? 'days'
        AND jsonb_typeof(document->'days') = 'array'
        AND document ? 'selections'
        AND jsonb_typeof(document->'selections') = 'array'
        AND document ? 'arrangements'
        AND jsonb_typeof(document->'arrangements') = 'object'
        AND jsonb_typeof(document #> '{arrangements,lodgings}') = 'array'
        AND jsonb_typeof(document #> '{arrangements,intercity_trains}') = 'array'
        AND document ? 'totals'
        AND jsonb_typeof(document->'totals') = 'object'
        AND document ? 'health'
        AND jsonb_typeof(document->'health') = 'object'
    );

ALTER TABLE travel_v2.trip_revisions
    DROP CONSTRAINT chk_travel_v2_revision_snapshot;
ALTER TABLE travel_v2.trip_revisions
    ADD CONSTRAINT chk_travel_v2_revision_snapshot CHECK (
        jsonb_typeof(snapshot) = 'object'
        AND snapshot ? 'schema_version'
        AND snapshot->'schema_version' = '2'::jsonb
        AND snapshot ? 'days'
        AND jsonb_typeof(snapshot->'days') = 'array'
        AND snapshot ? 'arrangements'
        AND jsonb_typeof(snapshot->'arrangements') = 'object'
        AND jsonb_typeof(snapshot #> '{arrangements,lodgings}') = 'array'
        AND jsonb_typeof(snapshot #> '{arrangements,intercity_trains}') = 'array'
    );
