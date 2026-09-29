-- 059_security_events.sql — 安全事件统一表（M9 / 台账 D9）
-- 目标：安全事件此前分散且大半不可统计——403 权限拒绝 / JWT 失败零记录
--   （静默 403），Input Guard 拦截只在 Prometheus label，Evidence Gate 拒答
--   原因只在 trace JSON。无法回答「谁在越权、被拦了多少、为什么拒答」。
-- 本表是安全事件的统一落点（旁路追加，写入失败降级日志不阻断主流程）：
--   INPUT_GUARD_BLOCK  输入门禁拦截（category=guard 分类）
--   AUTHZ_DENIED       管理端/业务 403（deps/rbac 拒绝点）
--   JWT_INVALID        应用层 JWT 校验失败（过期/签名错）
--   EVIDENCE_REJECT    RAG 拒答（category=RejectReason 五类）
--   GATEWAY_DENIED     网关 401/403（Phase 2 从 gateway_access_logs 聚合回填）
-- detail 只存脱敏摘要（路径/风险级/阈值），禁止原文。
-- 幂等：IF NOT EXISTS。回滚：DROP TABLE。

CREATE TABLE IF NOT EXISTS ai.security_events (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT '',
    user_id     TEXT NOT NULL DEFAULT '',
    event_type  TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    trace_id    TEXT NOT NULL DEFAULT '',
    request_id  TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    detail      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_security_events_type CHECK (event_type IN (
        'INPUT_GUARD_BLOCK', 'AUTHZ_DENIED', 'JWT_INVALID',
        'EVIDENCE_REJECT', 'GATEWAY_DENIED'))
);

CREATE INDEX IF NOT EXISTS idx_security_events_type_ts
    ON ai.security_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_user_ts
    ON ai.security_events(user_id, created_at DESC);
