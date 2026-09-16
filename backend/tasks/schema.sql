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
    error_type      VARCHAR(128) NOT NULL DEFAULT '',     -- 异常类名（任务中心筛选/归因）
    traceback       TEXT         NULL,                    -- 失败堆栈（error_message 截断后仍可溯源）
    retry_count     INT          NOT NULL DEFAULT 0,
    celery_task_id  VARCHAR(64)  NOT NULL DEFAULT '',     -- Celery 异步结果 id（revoke 用）
    max_retries     INT          NOT NULL DEFAULT 3,      -- 任务级重试上限快照（创建时取配置）
    duration_ms     INT          NULL,                    -- 执行耗时（started_at → finished_at）
    queue           VARCHAR(64)  NOT NULL DEFAULT 'agent',-- 消费队列（水平扩展观测维度）
    worker          VARCHAR(128) NOT NULL DEFAULT '',      -- 执行节点（celery@host）
    trace_id        VARCHAR(64)  NOT NULL DEFAULT '',      -- 全链路追踪 id（可跳网关审计/日志）
    biz_type        VARCHAR(64)  NOT NULL DEFAULT '',      -- 业务归类（报告/工作流/审批…）
    biz_id          VARCHAR(128) NOT NULL DEFAULT '',      -- 业务对象 id（任务中心业务检索键）
    parent_task_id  UUID         NULL,                     -- 父任务（任务链/子任务）
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    queued_at       TIMESTAMPTZ  NULL,                     -- apply_async 成功时刻（排队耗时起点）
    started_at      TIMESTAMPTZ  NULL,                     -- worker 拾取时刻（task_prerun）
    finished_at     TIMESTAMPTZ  NULL,                     -- 终态时刻（SUCCESS/FAILED/CANCELLED）
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 存量库升级：CREATE TABLE IF NOT EXISTS 不会补列，这里幂等补齐（2026-09-16 任务中心扩展）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS error_type      VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS traceback       TEXT         NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS max_retries     INT          NOT NULL DEFAULT 3;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS duration_ms     INT          NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS queue           VARCHAR(64)  NOT NULL DEFAULT 'agent';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS worker          VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS trace_id        VARCHAR(64)  NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS biz_type        VARCHAR(64)  NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS biz_id          VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id  UUID         NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS queued_at       TIMESTAMPTZ  NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS started_at      TIMESTAMPTZ  NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS finished_at     TIMESTAMPTZ  NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON tasks (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status) WHERE status IN ('PENDING', 'RUNNING', 'WAITING_USER', 'PAUSED');
-- 任务中心：全量状态历史筛选用（部分索引不覆盖 FAILED/SUCCESS 历史查询）
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks (created_at DESC);
-- revoke 反查 / 链路追踪 / 业务检索（条件索引，空串不进索引）
CREATE INDEX IF NOT EXISTS idx_tasks_celery_id ON tasks (celery_task_id) WHERE celery_task_id <> '';
CREATE INDEX IF NOT EXISTS idx_tasks_trace_id ON tasks (trace_id) WHERE trace_id <> '';
CREATE INDEX IF NOT EXISTS idx_tasks_biz ON tasks (biz_type, biz_id) WHERE biz_id <> '';

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
