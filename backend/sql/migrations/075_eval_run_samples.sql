-- 075_eval_run_samples.sql — 样本级 DB 层 + 引用删除保护
-- （2026-10-04 验收收敛三期 C3-2；DB-07 / DB-05 / DB-06）
--
-- 口径：
-- * 样本明细权威仍是 data/eval_runs/{run_id}/per_case/*.json（文件层），
--   本表是 (run_id, case_id, evaluator) 唯一约束的 DB 镜像（DB-07）——
--   重复消费同键不重复写（CON-07 巩固），SQL 可查、可对账。
-- * run_id 真外键 REFERENCES ai.eval_run_records(run_id) ON DELETE RESTRICT：
--   有样本引用的 run 不可物理删除（DB-06 删除保护），需先清样本。
-- * 写入侧 run_records.py 批量 upsert，开关 EVAL_SAMPLE_DB_SINK_ENABLED
--   （默认关，量大防拖慢主流程）。
--
-- 回滚：
--   DROP TABLE IF EXISTS ai.eval_run_samples;

CREATE TABLE IF NOT EXISTS ai.eval_run_samples (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES ai.eval_run_records(run_id) ON DELETE RESTRICT,
    case_id     TEXT NOT NULL,
    evaluator   TEXT NOT NULL DEFAULT 'self',
    status      TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'error', 'skip')),
    metrics     JSONB,
    error_stage TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, case_id, evaluator)
);

CREATE INDEX IF NOT EXISTS idx_eval_run_samples_run
    ON ai.eval_run_samples(run_id);

CREATE INDEX IF NOT EXISTS idx_eval_run_samples_status
    ON ai.eval_run_samples(run_id, status);
