-- 072_travel_decision_audit.sql — 旅游页用户决策留痕表（M4/G1，2026-10-04）
--
-- 为什么存在：草案应用/放弃、画布确认替换、档位切换、删减协商这些
-- 用户决策此前只存在于前端瞬时 state，事后无法回答「用户当时对哪一版
-- 行程做了什么决定」。逐条落库后按 conversation_id 可回放决策链。
-- 写入方：backend/travel/core/decision_store.py（软失败不挡 UI 动作）。
-- 消费方：GET /api/travel/decisions?conversation_id=（v1 只落库+查询，
-- 管理端页不做——2026-10-03 拍板）。

CREATE TABLE IF NOT EXISTS travel_decision_audit (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id       VARCHAR(128) NOT NULL DEFAULT '',
    user_id         VARCHAR(128) NOT NULL DEFAULT '',
    conversation_id VARCHAR(128) NOT NULL,
    -- apply_draft | discard_draft | canvas_replace | tier_switch | budget_negotiate
    decision        VARCHAR(32)  NOT NULL,
    plan_version    INTEGER      NOT NULL DEFAULT 0,
    tier_from       VARCHAR(32)  NOT NULL DEFAULT '',
    tier_to         VARCHAR(32)  NOT NULL DEFAULT '',
    -- 决策上下文（替换条目/协商话术/目标档位等，自由 JSON）
    payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- 触发该决策的消息来源（G3 口径：card_action/canvas_action/
    -- tier_switch/budget_negotiate/manual）与前端轮次标识
    source          VARCHAR(32)  NOT NULL DEFAULT '',
    client_run_id   VARCHAR(64)  NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_travel_decision_conv_time
    ON travel_decision_audit (conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_travel_decision_user_time
    ON travel_decision_audit (user_id, created_at DESC);
