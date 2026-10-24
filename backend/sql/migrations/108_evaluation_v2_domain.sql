-- 评测平台 V2：隔离的领域 schema、版本快照、租户 RLS 和追加审计。
-- 仅新增对象；不覆盖旧版 eval 表、静态数据集或运行产物。

CREATE SCHEMA IF NOT EXISTS evaluation_v2;
REVOKE ALL ON SCHEMA evaluation_v2 FROM PUBLIC;

-- 应用为 postgres 超级用户连接时不能依靠 FORCE RLS；V2 repository 每个事务
-- 必须 SET LOCAL ROLE 到此 NOSUPERUSER/NOBYPASSRLS 角色后再设置可信 tenant。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evaluation_v2_app') THEN
        CREATE ROLE evaluation_v2_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;
ALTER ROLE evaluation_v2_app NOSUPERUSER NOBYPASSRLS;
GRANT evaluation_v2_app TO CURRENT_USER;
GRANT USAGE ON SCHEMA evaluation_v2 TO evaluation_v2_app;

CREATE OR REPLACE FUNCTION evaluation_v2.current_tenant_id()
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.tenant_id', true), '')
$$;

CREATE OR REPLACE FUNCTION evaluation_v2.reject_immutable_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'evaluation_v2 append-only or published records cannot be changed'
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION evaluation_v2.reject_published_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'published' THEN
        RAISE EXCEPTION 'published evaluation versions are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_datasets (
    dataset_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    dataset_key      VARCHAR(64) NOT NULL,
    name             VARCHAR(160) NOT NULL,
    domain           VARCHAR(64) NOT NULL DEFAULT 'rag',
    description      TEXT NOT NULL DEFAULT '',
    owner_user_id    VARCHAR(128) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, dataset_key),
    UNIQUE (tenant_id, dataset_id)
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_cases (
    case_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    source_kind      VARCHAR(32) NOT NULL
        CHECK (source_kind IN ('manual', 'import', 'document_synthesized', 'trace', 'legacy')),
    lifecycle_status VARCHAR(24) NOT NULL DEFAULT 'candidate'
        CHECK (lifecycle_status IN ('candidate', 'approved', 'published', 'archived')),
    created_by       VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, case_id)
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_case_revisions (
    revision_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    case_id          UUID NOT NULL,
    revision         INTEGER NOT NULL CHECK (revision > 0),
    input_json       JSONB NOT NULL,
    expected_json    JSONB NOT NULL,
    evidence_json    JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(evidence_json) = 'array'),
    metadata_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_ref       JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_content_hash CHAR(64) NOT NULL DEFAULT '',
    content_hash     CHAR(64) NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    verified_by      VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, case_id, revision),
    UNIQUE (tenant_id, revision_id),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES evaluation_v2.eval_cases (tenant_id, case_id) ON DELETE RESTRICT,
    CHECK (jsonb_typeof(input_json) = 'object'),
    CHECK (jsonb_typeof(expected_json) = 'object'),
    CHECK (jsonb_typeof(metadata_json) = 'object'),
    CHECK (jsonb_typeof(source_ref) = 'object')
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_candidates (
    candidate_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    source_kind        VARCHAR(32) NOT NULL
        CHECK (source_kind IN ('manual', 'import', 'document_synthesized', 'trace', 'feedback', 'legacy')),
    payload_json       JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    privacy_status     VARCHAR(24) NOT NULL DEFAULT 'pending'
        CHECK (privacy_status IN ('pending', 'redacted', 'blocked')),
    review_status      VARCHAR(24) NOT NULL DEFAULT 'draft'
        CHECK (review_status IN ('draft', 'needs_evidence', 'in_review', 'approved', 'rejected')),
    revision           INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    duplicate_key      CHAR(64) NOT NULL DEFAULT '',
    generation_job_id  UUID,
    idempotency_key    VARCHAR(128) NOT NULL,
    promoted_case_id   UUID,
    created_by         VARCHAR(128) NOT NULL,
    updated_by         VARCHAR(128) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, candidate_id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, promoted_case_id)
        REFERENCES evaluation_v2.eval_cases (tenant_id, case_id) ON DELETE RESTRICT,
    CHECK (source_kind <> 'trace' OR NOT (payload_json ? 'expected'))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_annotations (
    annotation_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    candidate_id     UUID NOT NULL,
    candidate_revision INTEGER NOT NULL CHECK (candidate_revision > 0),
    reviewer_id      VARCHAR(128) NOT NULL,
    verdict          VARCHAR(16) NOT NULL CHECK (verdict IN ('approved', 'rejected', 'needs_changes')),
    changed_fields   JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale        TEXT NOT NULL CHECK (length(btrim(rationale)) > 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, candidate_id)
        REFERENCES evaluation_v2.eval_candidates (tenant_id, candidate_id) ON DELETE RESTRICT,
    CHECK (jsonb_typeof(changed_fields) = 'object'),
    CHECK (jsonb_typeof(evidence_json) = 'array')
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_dataset_versions (
    dataset_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    dataset_id         UUID NOT NULL,
    version            VARCHAR(64) NOT NULL,
    status             VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published')),
    content_hash       CHAR(64) NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    manifest_json      JSONB NOT NULL CHECK (jsonb_typeof(manifest_json) = 'object'),
    artifact_uri       TEXT NOT NULL DEFAULT '',
    published_by       VARCHAR(128),
    published_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, dataset_version_id),
    UNIQUE (tenant_id, dataset_id, version),
    FOREIGN KEY (tenant_id, dataset_id)
        REFERENCES evaluation_v2.eval_datasets (tenant_id, dataset_id) ON DELETE RESTRICT,
    CHECK ((status = 'draft' AND published_by IS NULL AND published_at IS NULL)
        OR (status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_dataset_items (
    tenant_id          VARCHAR(128) NOT NULL,
    dataset_version_id UUID NOT NULL,
    case_id            UUID NOT NULL,
    case_revision      INTEGER NOT NULL,
    ordinal            INTEGER NOT NULL CHECK (ordinal >= 0),
    weight             NUMERIC(8,4) NOT NULL DEFAULT 1 CHECK (weight > 0),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dataset_version_id, case_id),
    UNIQUE (tenant_id, dataset_version_id, ordinal),
    FOREIGN KEY (tenant_id, dataset_version_id)
        REFERENCES evaluation_v2.eval_dataset_versions (tenant_id, dataset_version_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, case_id, case_revision)
        REFERENCES evaluation_v2.eval_case_revisions (tenant_id, case_id, revision) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION evaluation_v2.reject_published_dataset_item_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_version UUID;
    target_tenant VARCHAR(128);
BEGIN
    target_version := COALESCE(NEW.dataset_version_id, OLD.dataset_version_id);
    target_tenant := COALESCE(NEW.tenant_id, OLD.tenant_id);
    IF EXISTS (
        SELECT 1 FROM evaluation_v2.eval_dataset_versions
        WHERE tenant_id = target_tenant
          AND dataset_version_id = target_version
          AND status = 'published'
    ) THEN
        RAISE EXCEPTION 'published dataset items are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_eval_v2_dataset_item_immutable ON evaluation_v2.eval_dataset_items;
CREATE TRIGGER trg_eval_v2_dataset_item_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON evaluation_v2.eval_dataset_items
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_published_dataset_item_mutation();

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_suites (
    suite_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    suite_key        VARCHAR(64) NOT NULL,
    name             VARCHAR(160) NOT NULL,
    module           VARCHAR(64) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_by       VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, suite_id),
    UNIQUE (tenant_id, suite_key)
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_evaluator_versions (
    evaluator_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            VARCHAR(128) NOT NULL,
    evaluator_key        VARCHAR(64) NOT NULL,
    version              VARCHAR(64) NOT NULL,
    evaluator_kind       VARCHAR(24) NOT NULL
        CHECK (evaluator_kind IN ('deterministic', 'evidence', 'llm_judge', 'human')),
    config_json          JSONB NOT NULL CHECK (jsonb_typeof(config_json) = 'object'),
    rubric_hash          CHAR(64) NOT NULL CHECK (rubric_hash ~ '^[a-f0-9]{64}$'),
    status               VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published')),
    created_by           VARCHAR(128) NOT NULL,
    published_by         VARCHAR(128),
    published_at         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, evaluator_version_id),
    UNIQUE (tenant_id, evaluator_key, version),
    CHECK ((status = 'draft' AND published_by IS NULL AND published_at IS NULL)
        OR (status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_suite_versions (
    suite_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    suite_id         UUID NOT NULL,
    version          VARCHAR(64) NOT NULL,
    dataset_version_id UUID NOT NULL,
    case_selection   JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluator_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
    metric_policy    JSONB NOT NULL DEFAULT '{}'::jsonb,
    security_policy  JSONB NOT NULL DEFAULT '{}'::jsonb,
    minimum_valid_samples INTEGER NOT NULL CHECK (minimum_valid_samples > 0),
    status           VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published')),
    snapshot_hash    CHAR(64) NOT NULL CHECK (snapshot_hash ~ '^[a-f0-9]{64}$'),
    published_by     VARCHAR(128),
    published_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, suite_version_id),
    UNIQUE (tenant_id, suite_id, version),
    FOREIGN KEY (tenant_id, suite_id)
        REFERENCES evaluation_v2.eval_suites (tenant_id, suite_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, dataset_version_id)
        REFERENCES evaluation_v2.eval_dataset_versions (tenant_id, dataset_version_id) ON DELETE RESTRICT,
    CHECK (jsonb_typeof(case_selection) = 'object'),
    CHECK (jsonb_typeof(evaluator_versions) = 'array'),
    CHECK (jsonb_typeof(metric_policy) = 'object'),
    CHECK (jsonb_typeof(security_policy) = 'object'),
    CHECK ((status = 'draft' AND published_by IS NULL AND published_at IS NULL)
        OR (status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_corpus_snapshots (
    corpus_snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    kb_id              VARCHAR(64) NOT NULL,
    document_versions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    index_generation   VARCHAR(128) NOT NULL,
    permissions_digest CHAR(64) NOT NULL CHECK (permissions_digest ~ '^[a-f0-9]{64}$'),
    content_hash       CHAR(64) NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    artifact_uri       TEXT NOT NULL DEFAULT '',
    captured_by        VARCHAR(128) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, corpus_snapshot_id),
    CHECK (jsonb_typeof(document_versions) = 'array')
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_generation_jobs (
    job_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    corpus_snapshot_id UUID NOT NULL,
    request_id         VARCHAR(128) NOT NULL,
    operation          VARCHAR(24) NOT NULL CHECK (operation IN ('candidate_generation', 'synthesis')),
    job_status         VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (job_status IN ('queued', 'preparing', 'running', 'completed', 'failed', 'cancelled', 'blocked')),
    requested_count    INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 100),
    completed_count    INTEGER NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
    progress_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost_amount        NUMERIC(18,8),
    cost_currency      CHAR(3),
    error_code         VARCHAR(64) NOT NULL DEFAULT '',
    created_by         VARCHAR(128) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    UNIQUE (tenant_id, job_id),
    UNIQUE (tenant_id, request_id),
    FOREIGN KEY (tenant_id, corpus_snapshot_id)
        REFERENCES evaluation_v2.eval_corpus_snapshots (tenant_id, corpus_snapshot_id) ON DELETE RESTRICT,
    CHECK (completed_count <= requested_count),
    CHECK (jsonb_typeof(progress_json) = 'object'),
    CHECK ((cost_amount IS NULL AND cost_currency IS NULL) OR (cost_amount >= 0 AND cost_currency IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_feedback_intake (
    intake_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    source_kind        VARCHAR(16) NOT NULL CHECK (source_kind IN ('trace', 'feedback', 'drift')),
    trace_ref          VARCHAR(160) NOT NULL,
    trigger_kind       VARCHAR(64) NOT NULL,
    privacy_status     VARCHAR(16) NOT NULL CHECK (privacy_status IN ('pending', 'redacted', 'blocked')),
    review_status      VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'reviewed', 'promoted', 'rejected')),
    assigned_to        VARCHAR(128) NOT NULL DEFAULT '',
    expires_at         TIMESTAMPTZ NOT NULL,
    created_by         VARCHAR(128) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, intake_id),
    UNIQUE (tenant_id, trace_ref, trigger_kind)
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_runs (
    run_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    suite_version_id   UUID NOT NULL,
    corpus_snapshot_id UUID NOT NULL,
    request_id         VARCHAR(128) NOT NULL,
    job_status         VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (job_status IN ('queued', 'preparing', 'running', 'aggregating', 'completed', 'failed', 'cancelled', 'blocked')),
    gate_verdict       VARCHAR(16)
        CHECK (gate_verdict IS NULL OR gate_verdict IN ('PASS', 'FAIL', 'INVALID')),
    gate_reason        JSONB NOT NULL DEFAULT '[]'::jsonb,
    snapshot_json      JSONB NOT NULL CHECK (jsonb_typeof(snapshot_json) = 'object'),
    env_hash           CHAR(64) NOT NULL CHECK (env_hash ~ '^[a-f0-9]{64}$'),
    total_cases        INTEGER NOT NULL DEFAULT 0 CHECK (total_cases >= 0),
    completed_cases    INTEGER NOT NULL DEFAULT 0 CHECK (completed_cases >= 0),
    passed_cases       INTEGER NOT NULL DEFAULT 0 CHECK (passed_cases >= 0),
    cost_amount        NUMERIC(18,8),
    cost_currency      CHAR(3),
    invalid_reason     VARCHAR(64) NOT NULL DEFAULT '',
    created_by         VARCHAR(128) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    UNIQUE (tenant_id, run_id),
    UNIQUE (tenant_id, request_id),
    FOREIGN KEY (tenant_id, suite_version_id)
        REFERENCES evaluation_v2.eval_suite_versions (tenant_id, suite_version_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, corpus_snapshot_id)
        REFERENCES evaluation_v2.eval_corpus_snapshots (tenant_id, corpus_snapshot_id) ON DELETE RESTRICT,
    CHECK (completed_cases <= total_cases),
    CHECK (passed_cases <= completed_cases),
    CHECK ((cost_amount IS NULL AND cost_currency IS NULL) OR (cost_amount >= 0 AND cost_currency IS NOT NULL)),
    CHECK (jsonb_typeof(gate_reason) = 'array'),
    CHECK (gate_verdict IS NULL OR job_status IN ('completed', 'failed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_case_results (
    result_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    run_id             UUID NOT NULL,
    case_id            UUID NOT NULL,
    case_revision      INTEGER NOT NULL,
    result_status      VARCHAR(12) NOT NULL CHECK (result_status IN ('pass', 'fail', 'error', 'skip')),
    scores_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    actual_artifact_uri TEXT NOT NULL DEFAULT '',
    trace_id           VARCHAR(160) NOT NULL DEFAULT '',
    failure_stage      VARCHAR(40) NOT NULL DEFAULT '',
    latency_ms         BIGINT CHECK (latency_ms IS NULL OR latency_ms >= 0),
    cost_amount        NUMERIC(18,8),
    cost_currency      CHAR(3),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, run_id, case_id),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES evaluation_v2.eval_runs (tenant_id, run_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, case_id, case_revision)
        REFERENCES evaluation_v2.eval_case_revisions (tenant_id, case_id, revision) ON DELETE RESTRICT,
    CHECK (jsonb_typeof(scores_json) = 'object'),
    CHECK ((cost_amount IS NULL AND cost_currency IS NULL) OR (cost_amount >= 0 AND cost_currency IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_experiments (
    experiment_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(128) NOT NULL,
    suite_version_id   UUID NOT NULL,
    name               VARCHAR(160) NOT NULL,
    baseline_run_id    UUID,
    candidate_run_id   UUID,
    created_by         VARCHAR(128) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, experiment_id),
    UNIQUE (tenant_id, name),
    FOREIGN KEY (tenant_id, suite_version_id)
        REFERENCES evaluation_v2.eval_suite_versions (tenant_id, suite_version_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, baseline_run_id)
        REFERENCES evaluation_v2.eval_runs (tenant_id, run_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, candidate_run_id)
        REFERENCES evaluation_v2.eval_runs (tenant_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_gate_decisions (
    decision_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    run_id           UUID NOT NULL,
    policy_hash      CHAR(64) NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    verdict          VARCHAR(16) NOT NULL CHECK (verdict IN ('PASS', 'FAIL', 'INVALID')),
    reasons_json     JSONB NOT NULL CHECK (jsonb_typeof(reasons_json) = 'array'),
    decided_by       VARCHAR(128) NOT NULL,
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES evaluation_v2.eval_runs (tenant_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_audit_events (
    event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(128) NOT NULL,
    actor_user_id    VARCHAR(128) NOT NULL,
    action           VARCHAR(64) NOT NULL,
    target_type      VARCHAR(48) NOT NULL,
    target_id        VARCHAR(128) NOT NULL,
    before_hash      CHAR(64) NOT NULL DEFAULT '',
    after_hash       CHAR(64) NOT NULL DEFAULT '',
    details_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(details_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_eval_v2_candidates_review
    ON evaluation_v2.eval_candidates (tenant_id, review_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_v2_candidates_duplicate
    ON evaluation_v2.eval_candidates (tenant_id, duplicate_key) WHERE duplicate_key <> '';
CREATE INDEX IF NOT EXISTS idx_eval_v2_versions_dataset
    ON evaluation_v2.eval_dataset_versions (tenant_id, dataset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_v2_runs_status
    ON evaluation_v2.eval_runs (tenant_id, job_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_v2_results_run
    ON evaluation_v2.eval_case_results (tenant_id, run_id, result_status);
CREATE INDEX IF NOT EXISTS idx_eval_v2_audit_target
    ON evaluation_v2.eval_audit_events (tenant_id, target_type, target_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA evaluation_v2 TO evaluation_v2_app;

-- 已发布版本仅可读；候选修订与结果/审核/裁决/审计只允许追加。
DROP TRIGGER IF EXISTS trg_eval_v2_dataset_version_immutable ON evaluation_v2.eval_dataset_versions;
CREATE TRIGGER trg_eval_v2_dataset_version_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_dataset_versions
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_published_version_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_suite_version_immutable ON evaluation_v2.eval_suite_versions;
CREATE TRIGGER trg_eval_v2_suite_version_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_suite_versions
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_published_version_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_evaluator_version_immutable ON evaluation_v2.eval_evaluator_versions;
CREATE TRIGGER trg_eval_v2_evaluator_version_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_evaluator_versions
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_published_version_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_revision_immutable ON evaluation_v2.eval_case_revisions;
CREATE TRIGGER trg_eval_v2_revision_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_case_revisions
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_annotation_immutable ON evaluation_v2.eval_annotations;
CREATE TRIGGER trg_eval_v2_annotation_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_annotations
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_result_immutable ON evaluation_v2.eval_case_results;
CREATE TRIGGER trg_eval_v2_result_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_case_results
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_gate_immutable ON evaluation_v2.eval_gate_decisions;
CREATE TRIGGER trg_eval_v2_gate_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_gate_decisions
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();
DROP TRIGGER IF EXISTS trg_eval_v2_audit_immutable ON evaluation_v2.eval_audit_events;
CREATE TRIGGER trg_eval_v2_audit_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_audit_events
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();

-- 所有租户资源都默认拒绝未设置可信 app.tenant_id 的请求。
DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'eval_datasets', 'eval_cases', 'eval_case_revisions', 'eval_candidates',
        'eval_annotations', 'eval_dataset_versions', 'eval_dataset_items',
        'eval_suites', 'eval_evaluator_versions', 'eval_suite_versions',
        'eval_corpus_snapshots', 'eval_generation_jobs', 'eval_feedback_intake',
        'eval_runs', 'eval_case_results', 'eval_experiments', 'eval_gate_decisions',
        'eval_audit_events'
    ] LOOP
        EXECUTE format('ALTER TABLE evaluation_v2.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE evaluation_v2.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_scope ON evaluation_v2.%I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_scope ON evaluation_v2.%I USING (tenant_id = evaluation_v2.current_tenant_id()) WITH CHECK (tenant_id = evaluation_v2.current_tenant_id())',
            table_name
        );
    END LOOP;
END $$;

COMMENT ON SCHEMA evaluation_v2 IS '评测平台 V2 权威领域；旧版 eval 表仅作迁移/运行 adapter';
COMMENT ON TABLE evaluation_v2.eval_dataset_versions IS 'Golden Dataset 不可变版本；published 行不可改写';
COMMENT ON TABLE evaluation_v2.eval_suite_versions IS '冻结 case selection、评分器与 Gate 策略的不可变方案版本';
COMMENT ON TABLE evaluation_v2.eval_runs IS '运行快照；job_status 与 gate_verdict 分离';
COMMENT ON TABLE evaluation_v2.eval_case_results IS '逐例结果索引；大体量敏感产物只保存受控 artifact URI';
COMMENT ON TABLE evaluation_v2.eval_gate_decisions IS '追加式 Gate 裁决历史，不以任务完成状态推导质量结果';
COMMENT ON TABLE evaluation_v2.eval_audit_events IS '评测域追加式审计事件';
