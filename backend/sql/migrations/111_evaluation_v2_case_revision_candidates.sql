-- 已审核 Golden Case 的新修订必须再次进入候选审核，不能原地覆盖发布数据。
ALTER TABLE evaluation_v2.eval_candidates
    ADD COLUMN IF NOT EXISTS revision_target_case_id UUID,
    ADD COLUMN IF NOT EXISTS revision_base_number INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'eval_candidates_revision_target_fk'
          AND conrelid = 'evaluation_v2.eval_candidates'::regclass
    ) THEN
        ALTER TABLE evaluation_v2.eval_candidates
            ADD CONSTRAINT eval_candidates_revision_target_fk
            FOREIGN KEY (tenant_id, revision_target_case_id)
            REFERENCES evaluation_v2.eval_cases (tenant_id, case_id)
            ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'eval_candidates_revision_target_pair_ck'
          AND conrelid = 'evaluation_v2.eval_candidates'::regclass
    ) THEN
        ALTER TABLE evaluation_v2.eval_candidates
            ADD CONSTRAINT eval_candidates_revision_target_pair_ck
            CHECK (
                (revision_target_case_id IS NULL AND revision_base_number IS NULL)
                OR (revision_target_case_id IS NOT NULL AND revision_base_number > 0)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_eval_candidates_revision_target
    ON evaluation_v2.eval_candidates (tenant_id, revision_target_case_id, revision_base_number)
    WHERE revision_target_case_id IS NOT NULL;

COMMENT ON COLUMN evaluation_v2.eval_candidates.revision_target_case_id IS
    '候选审核通过后要追加修订的已审核 Case；NULL 表示创建新 Case';
COMMENT ON COLUMN evaluation_v2.eval_candidates.revision_base_number IS
    '创建修订候选时看到的 Case revision；审核时作 CAS，防止过期草稿覆盖新版本';
