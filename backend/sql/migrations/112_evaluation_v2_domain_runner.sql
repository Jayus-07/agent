-- 非 RAG 评测不绑定知识库语料；Run 仍冻结 Golden、Suite 与执行环境。
ALTER TABLE evaluation_v2.eval_runs
    ALTER COLUMN corpus_snapshot_id DROP NOT NULL;
