package com.agent.cs.statemachine;

import com.agent.cs.common.ApiException;

import java.util.Set;
import java.util.regex.Pattern;

/**
 * 人工转接状态机（直译自 backend/customer_service/handoff.py）
 *
 * 5 状态流转:
 *   AI_ACTIVE → HANDOFF_REQUESTED → WAITING_HUMAN → HUMAN_ACTIVE → CLOSED
 *   HANDOFF_REQUESTED → AI_ACTIVE (回退)
 */
public final class HandoffStateMachine {

    public enum HandoffState {
        AI_ACTIVE("ai_active"),
        HANDOFF_REQUESTED("handoff_requested"),
        WAITING_HUMAN("waiting_human"),
        HUMAN_ACTIVE("human_active"),
        CLOSED("closed");

        public final String value;

        HandoffState(String value) { this.value = value; }

        public static HandoffState of(String value) {
            for (HandoffState s : values()) {
                if (s.value.equals(value)) return s;
            }
            throw ApiExceptionFactory.invalidTransition("handoff_state", "unknown", value);
        }

        public boolean isTerminal() { return this == CLOSED; }

        /** 是否需要拦截业务请求（对齐 INTERCEPT_STATES / should_intercept） */
        public boolean shouldIntercept() {
            return this == HANDOFF_REQUESTED || this == WAITING_HUMAN || this == HUMAN_ACTIVE;
        }
    }

    public enum TriggerType {
        EXPLICIT_REQUEST("explicit_request"),
        LOW_CONFIDENCE("low_confidence"),
        CONSECUTIVE_FAILURES("consecutive_failures"),
        COMPLAINT_ESCALATION("complaint_escalation"),
        HIGH_RISK_ACTION("high_risk_action"),
        NONE("none");

        public final String value;

        TriggerType(String value) { this.value = value; }
    }

    /** 转人工触发信号 */
    public record HandoffTrigger(TriggerType triggerType, String reason, double confidence) {
    }

    public record TransitionResult(HandoffState state, boolean changed) {
    }

    private static final Set<String> HANDOFF_KEYWORDS = Set.of(
            "人工", "真人", "转接", "找客服", "转人工",
            "不要机器人", "找经理", "找主管"
    );

    private static final Pattern[] HANDOFF_PATTERNS = {
            Pattern.compile("(转|找).*(人工|真人|客服|经理|主管)"),
            Pattern.compile("人工服务"),
            Pattern.compile("不要.*机器人"),
            Pattern.compile("你是.*机器人.*吗"),
    };

    private HandoffStateMachine() {
    }

    public static TransitionResult transition(HandoffState current, HandoffState target) {
        if (target == current) {
            return new TransitionResult(current, false);
        }
        boolean allowed = switch (current) {
            case AI_ACTIVE -> target == HandoffState.HANDOFF_REQUESTED || target == HandoffState.CLOSED;
            case HANDOFF_REQUESTED -> target == HandoffState.WAITING_HUMAN
                    || target == HandoffState.AI_ACTIVE || target == HandoffState.CLOSED;
            case WAITING_HUMAN -> target == HandoffState.HUMAN_ACTIVE || target == HandoffState.CLOSED;
            case HUMAN_ACTIVE -> target == HandoffState.CLOSED;
            case CLOSED -> false;
        };
        if (!allowed) {
            throw ApiException.businessRule(
                    "Invalid handoff transition: %s → %s".formatted(current.value, target.value));
        }
        return new TransitionResult(target, true);
    }

    /**
     * 从用户文本检测显式转人工请求（对齐 detect_handoff_trigger）
     */
    public static HandoffTrigger detectHandoffTrigger(String text) {
        if (text == null) return null;
        String stripped = text.strip();
        if (stripped.isEmpty()) return null;

        for (Pattern p : HANDOFF_PATTERNS) {
            if (p.matcher(stripped).find()) {
                return new HandoffTrigger(
                        TriggerType.EXPLICIT_REQUEST,
                        "用户显式请求人工服务: " + stripped.substring(0, Math.min(50, stripped.length())),
                        1.0);
            }
        }
        for (String kw : HANDOFF_KEYWORDS) {
            if (stripped.contains(kw)) {
                return new HandoffTrigger(
                        TriggerType.EXPLICIT_REQUEST,
                        "用户显式请求人工服务: 关键词 '" + kw + "'",
                        1.0);
            }
        }
        return null;
    }

    /**
     * 自动触发评估（对齐 evaluate_auto_triggers；阈值由调用方传入）
     *
     * @param consecutiveLowConfidence   连续低置信度轮数
     * @param lastConfidence             最近置信度
     * @param lowConfThreshold           低置信度阈值（CS_HANDOFF_LOW_CONF_THRESHOLD）
     * @param consecutiveFailures        连续失败次数
     * @param consecutiveFailLimit       失败上限（CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT）
     */
    public static HandoffTrigger evaluateAutoTriggers(
            int consecutiveLowConfidence, double lastConfidence, double lowConfThreshold,
            int consecutiveFailures, int consecutiveFailLimit) {
        if (consecutiveLowConfidence >= 2 && lastConfidence < lowConfThreshold) {
            return new HandoffTrigger(
                    TriggerType.LOW_CONFIDENCE,
                    "连续 %d 次低置信度 (最近: %.2f)".formatted(consecutiveLowConfidence, lastConfidence),
                    lastConfidence);
        }
        if (consecutiveFailures >= consecutiveFailLimit) {
            return new HandoffTrigger(
                    TriggerType.CONSECUTIVE_FAILURES,
                    "连续 %d 次回答失败".formatted(consecutiveFailures),
                    1.0);
        }
        return null;
    }
}
