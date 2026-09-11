package com.agent.cs.statemachine;

import com.agent.cs.common.ApiException;

import java.time.OffsetDateTime;
import java.util.Set;

/**
 * 确认状态机（直译自 backend/customer_service/confirmation.py）
 *
 * NOT_REQUIRED → EXECUTING → SUCCESS / FAILED
 * PENDING → CONFIRMED → EXECUTING → SUCCESS / FAILED
 * PENDING → CANCELLED / EXPIRED
 * PENDING → EXECUTING 非法（必须经过 CONFIRMED）
 */
public final class ConfirmationStateMachine {

    public enum ConfirmationState {
        NOT_REQUIRED("not_required"),
        PENDING_CONFIRMATION("pending"),
        USER_CONFIRMED("confirmed"),
        USER_CANCELLED("cancelled"),
        EXECUTING("executing"),
        SUCCESS("success"),
        FAILED("failed"),
        EXPIRED("expired");

        public final String value;

        ConfirmationState(String value) { this.value = value; }

        public static ConfirmationState of(String value) {
            for (ConfirmationState s : values()) {
                if (s.value.equals(value)) return s;
            }
            throw ApiExceptionFactory.invalidTransition("confirmation_state", "unknown", value);
        }

        public boolean isTerminal() {
            return this == SUCCESS || this == FAILED || this == USER_CANCELLED || this == EXPIRED;
        }
    }

    public enum ConfirmationIntent {
        CONFIRM, CANCEL, NONE
    }

    public record TransitionResult(ConfirmationState state, boolean changed) {
    }

    private static final Set<String> CONFIRM_KEYWORDS = Set.of(
            "确认", "确定", "好的", "同意", "可以", "没问题", "是的",
            "嗯", "对", "ok", "yes", "confirm"
    );

    private static final Set<String> CANCEL_KEYWORDS = Set.of(
            "取消", "算了", "不要", "不", "否", "放弃", "算了不",
            "cancel", "no"
    );

    private ConfirmationStateMachine() {
    }

    public static TransitionResult transition(ConfirmationState current, ConfirmationState target) {
        if (target == current) {
            return new TransitionResult(current, false);
        }
        boolean allowed = switch (current) {
            case NOT_REQUIRED -> target == ConfirmationState.EXECUTING;
            case PENDING_CONFIRMATION -> target == ConfirmationState.USER_CONFIRMED
                    || target == ConfirmationState.USER_CANCELLED || target == ConfirmationState.EXPIRED;
            case USER_CONFIRMED -> target == ConfirmationState.EXECUTING;
            case EXECUTING -> target == ConfirmationState.SUCCESS || target == ConfirmationState.FAILED;
            default -> false;
        };
        if (!allowed) {
            throw ApiException.businessRule(
                    "Invalid confirmation transition: %s → %s".formatted(current.value, target.value));
        }
        return new TransitionResult(target, true);
    }

    /**
     * 检测用户文本是确认还是取消（对齐 detect_confirmation_intent）
     */
    public static ConfirmationIntent detectConfirmationIntent(String text) {
        if (text == null) return ConfirmationIntent.NONE;
        String lower = text.strip().toLowerCase();
        if (lower.isEmpty()) return ConfirmationIntent.NONE;

        for (String kw : CANCEL_KEYWORDS) {
            if (lower.contains(kw)) return ConfirmationIntent.CANCEL;
        }
        for (String kw : CONFIRM_KEYWORDS) {
            if (lower.contains(kw)) return ConfirmationIntent.CONFIRM;
        }
        return ConfirmationIntent.NONE;
    }

    public static OffsetDateTime computeExpiresAt(OffsetDateTime createdAt, long ttlSeconds) {
        return createdAt.plusSeconds(ttlSeconds);
    }

    public static boolean isExpired(OffsetDateTime expiresAt, OffsetDateTime now) {
        return expiresAt != null && now.isAfter(expiresAt) || now.equals(expiresAt);
    }
}
