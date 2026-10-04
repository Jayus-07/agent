-- 071_clarify_funnel_events.sql — 追问漏斗事件持久化（2026-10-03 企业口径）
--
-- 为什么存在：Prometheus Counter 是进程内口径，重启归零——漏斗的
-- 「月报级精确累计」从本表取数（消费方 GET /api/admin/clarify/stats 的
-- persisted 段），指标序列只承担实时 rate 监控。两口径同源双写：
-- backend/observability/clarify_funnel.py（指标入口即增 + PG 明细，
-- PG 失败降级为只有计数，软失败不影响主流程）。
-- 量级预期：追问卡为兜底路径（非正常流），事件量极低，无需分区。

CREATE TABLE IF NOT EXISTS ai.clarify_funnel_events (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- shown | clicked | resolved（与 Prometheus 序列一一对应）
    event_type  VARCHAR(16) NOT NULL,
    -- 追问卡来源（clarify_content 各卡 source）
    source      VARCHAR(64) NOT NULL DEFAULT '',
    session_id  VARCHAR(128) NOT NULL DEFAULT '',
    trace_id    VARCHAR(64)  NOT NULL DEFAULT '',
    detail      JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_clarify_funnel_type_time
    ON ai.clarify_funnel_events (event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clarify_funnel_created_at
    ON ai.clarify_funnel_events (created_at DESC);
