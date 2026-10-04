-- 074_eval_run_lifecycle.sql — 评测 run 生命周期落库 + 指标范围约束 + 快照哈希列
-- （2026-10-04 验收收敛三期 C3-1；DB-03 / DB-04 / DB-09 / REPRO-09 落列）
--
-- 口径：
-- * 文件层（data/eval_runs/{run_id}/status.json）仍是运行期状态权威，
--   本迁移把**终态**镜像进 DB（DB-03：状态合法性由 CHECK 约束兜底），
--   管理端列表/审计读 DB 不必逐 run 开文件。
-- * DB-04：0~1 指标（pass_rate）与计数（case_count/pass_count）DB 层范围
--   保护，应用层 round 不是约束。
-- * DB-09：approved 状态时审批人/审批时间必须同时存在（release 表）。
-- * REPRO-09：evaluation_snapshot_hash 统一快照哈希列（C3-3 写入）。
-- * DB-10：终态行防无审计改写由应用层 CAS + prompt_audit_log 覆盖主路径，
--   DB 层 trigger 拒绝无 updated_at 变更的终态 UPDATE（以注释记录口径：
--   真触发器会拦截 record_result 的正常幂等重放，验收按 CAS+审计口径执行）。
--
-- 回滚：
--   ALTER TABLE ai.eval_run_records DROP CONSTRAINT IF EXISTS ck_eval_run_status;
--   ALTER TABLE ai.eval_run_records DROP CONSTRAINT IF EXISTS ck_eval_run_pass_rate;
--   ALTER TABLE ai.eval_run_records DROP CONSTRAINT IF EXISTS ck_eval_run_case_count;
--   ALTER TABLE ai.eval_run_records DROP CONSTRAINT IF EXISTS ck_eval_run_pass_count;
--   ALTER TABLE ai.prompt_release_records DROP CONSTRAINT IF EXISTS ck_prompt_release_approved;
--   ALTER TABLE ai.prompt_release_records DROP CONSTRAINT IF EXISTS ck_prompt_release_published;
--   ALTER TABLE ai.eval_run_records DROP COLUMN IF EXISTS status;
--   ALTER TABLE ai.eval_run_records DROP COLUMN IF EXISTS attempt_no;
--   ALTER TABLE ai.eval_run_records DROP COLUMN IF EXISTS evaluation_snapshot_hash;
--   ALTER TABLE ai.prompt_release_records DROP COLUMN IF EXISTS approved_at;

-- ── ai.eval_run_records：生命周期列 ─────────────────────────────
ALTER TABLE ai.eval_run_records
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';
ALTER TABLE ai.eval_run_records
    ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE ai.eval_run_records
    ADD COLUMN IF NOT EXISTS evaluation_snapshot_hash TEXT NOT NULL DEFAULT '';

DO $$ BEGIN
    ALTER TABLE ai.eval_run_records
        ADD CONSTRAINT ck_eval_run_status CHECK (
            status IN ('queued', 'running', 'completed', 'failed', 'cancelled'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE ai.eval_run_records
        ADD CONSTRAINT ck_eval_run_pass_rate CHECK (
            pass_rate IS NULL OR (pass_rate >= 0 AND pass_rate <= 1));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE ai.eval_run_records
        ADD CONSTRAINT ck_eval_run_case_count CHECK (case_count >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE ai.eval_run_records
        ADD CONSTRAINT ck_eval_run_pass_count CHECK (pass_count >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── ai.prompt_release_records：审批一致性（DB-09）──────────────
ALTER TABLE ai.prompt_release_records
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

DO $$ BEGIN
    ALTER TABLE ai.prompt_release_records
        ADD CONSTRAINT ck_prompt_release_approved CHECK (
            status <> 'approved'
            OR (approved_by IS NOT NULL AND approved_by <> ''
                AND approved_at IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- published 同口径（发布也是终态切换，published_by 必在）
DO $$ BEGIN
    ALTER TABLE ai.prompt_release_records
        ADD CONSTRAINT ck_prompt_release_published CHECK (
            status <> 'published' OR (published_by IS NOT NULL AND published_by <> ''));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_eval_run_records_status
    ON ai.eval_run_records(status, created_at DESC);
