-- 人工复核绑定运行快照中的 Human Evaluator/Rubric，仅追加校准证据。
CREATE TABLE IF NOT EXISTS evaluation_v2.eval_human_reviews (
    review_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(128) NOT NULL,
    run_id              UUID NOT NULL,
    case_id             UUID NOT NULL,
    evaluator_version_id UUID NOT NULL,
    request_id          VARCHAR(128) NOT NULL,
    review_hash         CHAR(64) NOT NULL CHECK (review_hash ~ '^[a-f0-9]{64}$'),
    label               VARCHAR(24) NOT NULL CHECK (label IN (
                            'correct', 'partially_correct', 'incorrect', 'unsafe', 'unverifiable'
                        )),
    overall_score       NUMERIC(6,5) NOT NULL CHECK (overall_score >= 0 AND overall_score <= 1),
    scores_json         JSONB NOT NULL CHECK (jsonb_typeof(scores_json) = 'object'),
    rationale           VARCHAR(2000) NOT NULL CHECK (length(btrim(rationale)) >= 8),
    reviewed_by         VARCHAR(128) NOT NULL,
    reviewed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, request_id),
    UNIQUE (tenant_id, review_id),
    FOREIGN KEY (tenant_id, run_id, case_id)
        REFERENCES evaluation_v2.eval_case_results (tenant_id, run_id, case_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, evaluator_version_id)
        REFERENCES evaluation_v2.eval_evaluator_versions (tenant_id, evaluator_version_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_eval_v2_human_reviews_case
    ON evaluation_v2.eval_human_reviews (tenant_id, run_id, case_id, evaluator_version_id, reviewed_at DESC);

GRANT SELECT, INSERT ON evaluation_v2.eval_human_reviews TO evaluation_v2_app;

DROP TRIGGER IF EXISTS trg_eval_v2_human_review_immutable
    ON evaluation_v2.eval_human_reviews;
CREATE TRIGGER trg_eval_v2_human_review_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_human_reviews
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();

ALTER TABLE evaluation_v2.eval_human_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_v2.eval_human_reviews FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON evaluation_v2.eval_human_reviews;
CREATE POLICY tenant_scope ON evaluation_v2.eval_human_reviews
    USING (tenant_id = evaluation_v2.current_tenant_id())
    WITH CHECK (tenant_id = evaluation_v2.current_tenant_id());

COMMENT ON TABLE evaluation_v2.eval_human_reviews IS
    '人工评分记录，绑定运行快照中的不可变 Human Evaluator；不直接改变机器评分或 Gate 裁决';
