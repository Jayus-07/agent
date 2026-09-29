-- 060_task_operation_audits.sql — 任务操作审计表（M10 / 台账 D10）
-- 目标：admin retry/revoke/reexecute 此前只有 [AdminTaskAudit] 结构化日志
--   （不入库不可查）+ 网关访问日志（HTTP 层），任务详情页无法展示
--   「谁在何时对它做过什么、为什么」。本表补 DB 侧审计闭环。
-- reexecute 的克隆指向：new_task_id 记录克隆出的新任务（parent_task_id 反向
--   指向源任务，tasks 表已有该列）。
-- 幂等：IF NOT EXISTS。回滚：DROP TABLE。

CREATE TABLE IF NOT EXISTS ai.task_operation_audits (
    id            BIGSERIAL PRIMARY KEY,
    task_id       TEXT NOT NULL,
    new_task_id   TEXT,
    operation     TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT '',
    reason        TEXT NOT NULL DEFAULT '',
    before_status TEXT NOT NULL DEFAULT '',
    after_status  TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_task_op_operation CHECK (
        operation IN ('retry', 'revoke', 'reexecute', 'pause', 'resume', 'cancel'))
);

CREATE INDEX IF NOT EXISTS idx_task_op_audits_task
    ON ai.task_operation_audits(task_id, created_at DESC);
