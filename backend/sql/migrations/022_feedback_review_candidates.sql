-- 022_feedback_review_candidates.sql
-- WP5：反馈租户归属与评测候选审核状态机。

ALTER TABLE feedback ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_id TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS correction_text TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS expected_answer TEXT;
CREATE INDEX IF NOT EXISTS idx_feedback_trace ON feedback(tenant_id, trace_id);

CREATE TABLE IF NOT EXISTS feedback_candidates (
    candidate_id     TEXT PRIMARY KEY,
    feedback_id      BIGINT NOT NULL,
    tenant_id        TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    trace_id         TEXT NOT NULL,
    module           TEXT NOT NULL,
    case_json        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','promoted')),
    reviewer_id      TEXT DEFAULT '',
    review_note      TEXT DEFAULT '',
    promoted_case_id TEXT DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (tenant_id, trace_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_candidates_tenant_status
    ON feedback_candidates(tenant_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_candidates_trace
    ON feedback_candidates(tenant_id, trace_id);
