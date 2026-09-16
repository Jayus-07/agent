-- tasks/schema.sql — agent_memory 库任务编排表结构（幂等）
-- 应用方式一：task_service.ensure_schema() 启动时自动执行（CREATE TABLE IF NOT EXISTS）
-- 应用方式二：psql -h $PGHOST -U $PGUSER -d agent_memory -f backend/tasks/schema.sql

-- ── 任务主表 ────────────────────────────────────────────────
-- Celery 只传 id；Agent 执行状态（LangGraph checkpoint）定位靠 thread_id
CREATE TABLE IF NOT EXISTS tasks (
    id              UUID PRIMARY KEY,
    user_id         VARCHAR(128) NOT NULL,                -- 任务归属用户（隔离强制）
    tenant_id       VARCHAR(128) NOT NULL DEFAULT 'default',
    graph_name      VARCHAR(128) NOT NULL DEFAULT 'main', -- 预留多图路由，当前仅 main
    status          VARCHAR(32)  NOT NULL DEFAULT 'PENDING',
    input           JSONB        NOT NULL DEFAULT '{}',   -- {query, ...}
    output          JSONB        NULL,                    -- {answer, step_results}
    checkpoint_id   VARCHAR(128) NOT NULL DEFAULT '',     -- 最近 LangGraph checkpoint thread_id
    thread_id       VARCHAR(128) NOT NULL,                -- LangGraph checkpoint 线程标识
    current_node    VARCHAR(128) NOT NULL DEFAULT '',     -- 最近执行的图节点（进度查询）
    progress        VARCHAR(512) NOT NULL DEFAULT '',     -- 人类可读进度
    error_message   TEXT         NULL,
    retry_count     INT          NOT NULL DEFAULT 0,
    celery_task_id  VARCHAR(64)  NOT NULL DEFAULT '',     -- Celery 异步结果 id（revoke 用）
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON tasks (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status) WHERE status IN ('PENDING', 'RUNNING', 'WAITING_USER', 'PAUSED');

-- ── 节点级 checkpoint 历史 ──────────────────────────────────
-- 每个 Agent 节点执行完追加一行（节点输出快照），用于任务恢复/查询/审计。
-- LangGraph 自身的完整状态持久化由 PostgresSaver（checkpoints 系列表）负责，
-- 本表是任务视角的节点历史，二者互补。
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id          BIGSERIAL PRIMARY KEY,
    task_id     UUID         NOT NULL,
    node_name   VARCHAR(128) NOT NULL,
    state_json  JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_task ON agent_checkpoints (task_id, id);
