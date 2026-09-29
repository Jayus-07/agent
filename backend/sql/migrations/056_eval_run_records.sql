-- 056_eval_run_records.sql — 评测 run 台账（M7 / 台账 D7）
-- 目标：评测结果此前只落 data/eval_runs/ 文件目录——不可 SQL 查询、不可与
--   prompt/模型版本关联、无触发者。「改 prompt 必须跑评测」无法被机械验证。
-- 本表是**索引不是替代**：文件目录仍是明细权威（report.json/per_case），
-- 表只存 run 级摘要 + 版本指纹，供管理端 /evaluations 页与发布门禁查询。
-- 口径：
--   prompt_snapshot  PG 权威（prompts/service.snapshot_prompt_versions()），
--                    与 meta.json 的 yaml 扫描口径并存（meta.json 保留旧口径）
--   trigger          manual | prompt_publish | scheduled | release_gate | ci
--   metrics          每模块 {module: {pass_rate, total, passed, ...}}
-- 幂等：IF NOT EXISTS；run_id UNIQUE 支持重复 upsert（checkpoint 续跑场景）。

CREATE TABLE IF NOT EXISTS ai.eval_run_records (
    id                       BIGSERIAL PRIMARY KEY,
    run_id                   TEXT NOT NULL UNIQUE,
    module                   TEXT NOT NULL DEFAULT 'all',
    mode                     TEXT NOT NULL DEFAULT '',
    smoke                    BOOLEAN NOT NULL DEFAULT FALSE,
    dataset_version          JSONB NOT NULL DEFAULT '{}',
    git_sha                  TEXT NOT NULL DEFAULT '',
    prompt_snapshot          JSONB NOT NULL DEFAULT '{}',
    model_binding_fingerprint TEXT NOT NULL DEFAULT '',
    trigger                  TEXT NOT NULL DEFAULT 'manual',
    triggered_by             TEXT NOT NULL DEFAULT '',
    metrics                  JSONB NOT NULL DEFAULT '{}',
    case_count               INTEGER NOT NULL DEFAULT 0,
    pass_count               INTEGER NOT NULL DEFAULT 0,
    pass_rate                DOUBLE PRECISION,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_run_records_module_ts
    ON ai.eval_run_records(module, created_at DESC);
