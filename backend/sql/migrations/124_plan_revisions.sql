-- 124_plan_revisions.sql — 运行中任务的多轮自然语言计划修订（决策 #29）
-- 目标：用户对异步任务（tasks.execute_agent 链路）的执行计划做多轮自然语言
--   修改：draft（LLM 起草修订，服务端强制校验）→ apply（写回 checkpoint
--   plan 并经既有 resume 链重新入图）。复用既有主图/Checkpointer，不新建
--   第二执行器。
-- 语义边界（v1，诚实声明）：
--   - 只允许修改 pending/failed 步骤（修订步骤以新 step_id 重发，旧结果
--     保留为历史事实）；success 步骤只可保留复用或作废引用（从修订计划
--     移除），**已完成的外部写入不会被撤销或伪装回滚**——作废只是修订后
--     的计划不再引用该结果；
--   - running 步骤不可改（version 冲突保护）；本表不做计划快照的事实源，
--     事实源仍是 LangGraph checkpoint，本表记录修订意图与版本账本；
--   - 并发控制：base_plan_version ↔ 已应用修订的 new_version（无修订时
--     为 0），不匹配 → 409；UNIQUE(thread_id, new_version) 兜底并发 apply。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.plan_revisions;

CREATE TABLE IF NOT EXISTS ai.plan_revisions (
    id                TEXT PRIMARY KEY,              -- planrev_<hex>
    thread_id         TEXT NOT NULL,                 -- LangGraph checkpoint 线程（task-<task_id>）
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    user_id           TEXT NOT NULL,                 -- 归属用户（归属校验强制，非创建者 404）
    base_plan_version INT NOT NULL,                  -- 起草时基于的已应用版本（0 = 原始计划）
    new_version       INT NOT NULL,                  -- 应用后的版本号（base + 1）
    instruction       TEXT NOT NULL,                 -- 自然语言修改指令
    revised_plan      JSONB NOT NULL DEFAULT '{}',   -- normalize 后的修订计划 {"nodes":..., "edges":...}
    changes           JSONB NOT NULL DEFAULT '[]',   -- [{step_id, action: modified|kept|discarded, note}]
    status            TEXT NOT NULL DEFAULT 'draft', -- draft（草稿）| applied（已应用）| rejected（已拒绝/废弃）
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at        TIMESTAMPTZ,
    CONSTRAINT ck_plan_revisions_status CHECK (status IN ('draft', 'applied', 'rejected')),
    CONSTRAINT ck_plan_revisions_versions CHECK (new_version = base_plan_version + 1)
);

-- 并发 apply 兜底：同一 thread 的已应用版本号全局唯一（draft 可共存同号，
-- 先应用者胜出，败者在版本复核处 409）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_revisions_applied_version
    ON ai.plan_revisions (thread_id, new_version)
    WHERE status = 'applied';

CREATE INDEX IF NOT EXISTS idx_plan_revisions_thread
    ON ai.plan_revisions (thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plan_revisions_tenant
    ON ai.plan_revisions (tenant_id, created_at DESC);
