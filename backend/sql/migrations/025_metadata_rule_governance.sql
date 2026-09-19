-- 025_metadata_rule_governance.sql — 元数据动态规则版本治理
-- 写入路径：draft -> approved publish -> rollback；运行时只读 published 快照。

CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE IF NOT EXISTS ai.metadata_rule_versions (
    version          BIGSERIAL PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'rolled_back')),
    taxonomy_version TEXT NOT NULL,
    rules_hash       TEXT NOT NULL,
    actor            TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL DEFAULT '',
    approval_id      TEXT NOT NULL DEFAULT '',
    approved_by      TEXT NOT NULL DEFAULT '',
    effective_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_metadata_rule_versions_published
    ON ai.metadata_rule_versions(status) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_metadata_rule_versions_hash
    ON ai.metadata_rule_versions(rules_hash);

CREATE TABLE IF NOT EXISTS ai.metadata_rule_entries (
    version   BIGINT NOT NULL REFERENCES ai.metadata_rule_versions(version)
        ON DELETE CASCADE,
    keyword   TEXT NOT NULL CHECK (btrim(keyword) <> ''),
    doc_type  TEXT NOT NULL,
    category  TEXT NOT NULL DEFAULT '',
    weight    INTEGER NOT NULL DEFAULT 1 CHECK (weight BETWEEN 1 AND 10),
    enabled   INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    PRIMARY KEY (version, keyword)
);
CREATE INDEX IF NOT EXISTS idx_metadata_rule_entries_doc_type
    ON ai.metadata_rule_entries(version, doc_type, enabled);

-- 兼容已有 keyword_rules：只有不存在 published 快照时回填一次，部署升级
-- 不改变当前线上词表。hash 仅作为迁移基线标识，新草稿由服务层计算 SHA-256。
DO $$
DECLARE
    v_version BIGINT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ai.metadata_rule_versions WHERE status = 'published'
    ) AND to_regclass('public.keyword_rules') IS NOT NULL THEN
        INSERT INTO ai.metadata_rule_versions (
            status, taxonomy_version, rules_hash, actor, reason, effective_at
        ) VALUES (
            'published', 'v1', 'legacy-keyword-rules-v1', 'migration',
            'backfill existing keyword_rules', now()
        ) RETURNING version INTO v_version;

        INSERT INTO ai.metadata_rule_entries (
            version, keyword, doc_type, category, weight, enabled
        )
        SELECT v_version, keyword, doc_type, category,
               LEAST(GREATEST(weight, 1), 10),
               CASE WHEN enabled <> 0 THEN 1 ELSE 0 END
        FROM public.keyword_rules
        WHERE btrim(keyword) <> '';
    END IF;
END $$;
