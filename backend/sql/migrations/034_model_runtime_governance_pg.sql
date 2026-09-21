-- ============================================================
-- 034_model_runtime_governance_pg.sql — 模型运行时治理三件套
--
-- 内容：
--   1. llm_model_role_policy   角色级运行策略（fallback/timeout/retry/failure_policy）
--   2. llm_model_health        模型健康探测缓存（beat 定期探测 + 页面读缓存）
--   3. llm_config_history.object_type 纳入 'role_policy'（策略变更同样留审计）
--
-- 目标库：agent_memory（与 031 治理族同库）
-- 全部幂等，可重复执行。
-- ============================================================

-- ── 1. 角色级运行策略 ──────────────────────────────────────
-- 兼容性：role -> model 的既有绑定（llm_model_role_bindings）不动；
-- 本表是可选叠加层，无行时使用 config/model_roles.py::ROLE_RUNTIME_DEFAULTS。
-- fallback_model 存模型名（与绑定表口径一致），空 = 不启用 fallback。
CREATE TABLE IF NOT EXISTS llm_model_role_policy (
    role             TEXT PRIMARY KEY,
    fallback_model   TEXT NOT NULL DEFAULT '',
    timeout_seconds  INT  NOT NULL,
    max_retries      INT  NOT NULL DEFAULT 0,
    failure_policy   TEXT NOT NULL,
    updated_by       TEXT NOT NULL DEFAULT '',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT llm_model_role_policy_timeout_check
        CHECK (timeout_seconds BETWEEN 1 AND 600),
    CONSTRAINT llm_model_role_policy_retry_check
        CHECK (max_retries BETWEEN 0 AND 3),
    CONSTRAINT llm_model_role_policy_policy_check
        CHECK (failure_policy IN (
            'fallback', 'skip', 'fail_fast',
            'template_response', 'mark_failed'
        ))
);

-- ── 2. 模型健康探测缓存 ────────────────────────────────────
-- 探测由 Celery beat 周期执行（backend/tasks/model_health_tasks.py），
-- 管理端只读本表，绝不在页面打开时同步探测全部模型。
CREATE TABLE IF NOT EXISTS llm_model_health (
    model_name            TEXT PRIMARY KEY,
    provider              TEXT NOT NULL DEFAULT '',
    model_kind            TEXT NOT NULL DEFAULT 'chat',
    status                TEXT NOT NULL DEFAULT 'unknown',
    last_checked_at       TIMESTAMPTZ,
    last_latency_ms       INT,
    last_error            TEXT NOT NULL DEFAULT '',
    consecutive_failures  INT  NOT NULL DEFAULT 0,
    CONSTRAINT llm_model_health_status_check
        CHECK (status IN (
            'healthy', 'degraded', 'slow', 'rate_limited',
            'auth_failed', 'timeout', 'provider_unreachable',
            'model_not_found', 'unknown'
        ))
);

CREATE INDEX IF NOT EXISTS idx_llm_model_health_checked
    ON llm_model_health(last_checked_at DESC);

-- ── 3. 审计历史对象类型扩展 ────────────────────────────────
-- 031 建表时 CHECK 枚举没有 'role_policy'；这里放宽（幂等：重复执行结果一致）。
ALTER TABLE llm_config_history DROP CONSTRAINT IF EXISTS llm_config_history_object_type_check;
ALTER TABLE llm_config_history ADD CONSTRAINT llm_config_history_object_type_check
    CHECK (object_type IN (
        'role', 'provider', 'provider_credential',
        'provider_network_scope', 'role_policy'
    ));
