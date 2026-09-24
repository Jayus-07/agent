-- 049_task_pending_recovery.sql — Phase3 STOP B：PENDING Recovery Accelerator
-- 目标库：agent_memory（tasks 表）
-- 与 backend/tasks/schema.sql 保持同构（schema.sql 由 ensure_schema() 幂等
-- 执行；本文件供 db-migrate 流程/实库校验使用，两处语义一致）。
-- 注意：tasks 表历史演进权威在 backend/tasks/schema.sql（ensure_schema），
-- 本迁移是该次变更的实库执行留痕（Phase3 STOP B §33）。

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS dispatch_not_before_at TIMESTAMPTZ NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS pending_recovery_last_at TIMESTAMPTZ NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS pending_recovery_count INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_tasks_pending_queued ON tasks (queued_at)
    WHERE status = 'PENDING';
