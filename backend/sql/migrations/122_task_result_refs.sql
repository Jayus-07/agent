-- 122_task_result_refs.sql — 任务级受控结果引用（决策 #25）
-- 目标：多步任务里的大结果（默认 >8KB）不再整包驻留 step_results 与
--   LLM 上下文：
--   - Skill 输出在 BaseSkill 边界序列化后超阈值 → payload 落本表，
--     step_results 只留引用对象（ref_id + 摘要 + 行数 + schema 概要）；
--   - LLM 上下文（Reporter / 下游 prompt 组装）只见引用摘要，
--     不搬运整包 JSON；
--   - 真实数据只在受控解引用点（下游步骤参数注入、ref_value 字段取值）
--     按租户校验后取出；
--   - 失效策略：expires_at（默认 now()+24h）在 resolve 时强制校验，
--     cleanup_expired() 兜底 GC。
-- 语义（诚实边界）：
--   - 引用按任务级一次性传递设计，request_id/thread_id 仅留痕定位，
--     不做跨请求缓存命中；
--   - 租户隔离是硬校验（tenant 不匹配 resolve 抛 PermissionError）；
--     user_id 只随行留痕，不做行级 ACL（执行链身份已由上游授权收敛）；
--   - offload 落库失败在 Skill 边界软降级回内联（不阻断执行）；
--     resolve 失败显式报错，不静默回空。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.task_result_refs;

CREATE TABLE IF NOT EXISTS ai.task_result_refs (
    id             TEXT PRIMARY KEY,               -- taskref_<hex>
    request_id     TEXT NOT NULL DEFAULT '',
    thread_id      TEXT NOT NULL DEFAULT '',
    step_id        TEXT NOT NULL DEFAULT '',
    capability     TEXT NOT NULL DEFAULT '',
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    user_id        TEXT NOT NULL DEFAULT '',
    payload        JSONB NOT NULL,
    schema_summary JSONB NOT NULL DEFAULT '{}',
    row_count      INTEGER,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    expires_at     TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_result_refs_tenant_exp
    ON ai.task_result_refs(tenant_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_task_result_refs_request
    ON ai.task_result_refs(request_id);
