package com.agent.cs.statemachine;

/**
 * 会话双维度状态机（直译自 backend/customer_service/state_machine.py）
 *
 * conversation_status : open | pending | resolved | snoozed
 * handling_mode       : ai | human | waiting_human
 */
public final class ConversationStateMachine {

    public enum ConvStatus {
        OPEN("open"), PENDING("pending"), RESOLVED("resolved"), SNOOZED("snoozed");

        public final String value;

        ConvStatus(String value) { this.value = value; }

        public static ConvStatus of(String value) {
            for (ConvStatus s : values()) {
                if (s.value.equals(value)) return s;
            }
            throw ApiExceptionFactory.invalidTransition("conversation_status", "unknown", value);
        }
    }

    public enum HandlingMode {
        AI("ai"), HUMAN("human"), WAITING_HUMAN("waiting_human");

        public final String value;

        HandlingMode(String value) { this.value = value; }

        public static HandlingMode of(String value) {
            for (HandlingMode m : values()) {
                if (m.value.equals(value)) return m;
            }
            throw ApiExceptionFactory.invalidTransition("handling_mode", "unknown", value);
        }
    }

    /** 转换后状态快照 */
    public record TransitionResult(ConvStatus conversationStatus, HandlingMode handlingMode, boolean changed) {
        public String statusValue() { return conversationStatus.value; }
        public String modeValue() { return handlingMode.value; }
    }

    private ConversationStateMachine() {
    }

    public static TransitionResult transition(String currentStatus, String currentMode,
                                              String newStatus, String newMode,
                                              String assignedAgentId) {
        ConvStatus cur = ConvStatus.of(currentStatus);
        HandlingMode curMode = HandlingMode.of(currentMode);
        ConvStatus targetStatus = newStatus != null ? ConvStatus.of(newStatus) : cur;
        HandlingMode targetMode = newMode != null ? HandlingMode.of(newMode) : curMode;

        validateStatusTransition(cur, targetStatus);
        validateModeTransition(curMode, targetMode);
        checkInvariants(targetStatus, targetMode, assignedAgentId);

        boolean changed = targetStatus != cur || targetMode != curMode;
        return new TransitionResult(targetStatus, targetMode, changed);
    }

    private static void validateStatusTransition(ConvStatus src, ConvStatus dst) {
        if (dst == src) return;
        boolean allowed = switch (src) {
            case OPEN -> dst == ConvStatus.PENDING || dst == ConvStatus.RESOLVED || dst == ConvStatus.SNOOZED;
            case PENDING -> dst == ConvStatus.OPEN || dst == ConvStatus.RESOLVED;
            case RESOLVED -> dst == ConvStatus.OPEN;
            case SNOOZED -> dst == ConvStatus.OPEN;
        };
        if (!allowed) {
            throw ApiExceptionFactory.invalidTransition("conversation_status", src.value, dst.value);
        }
    }

    private static void validateModeTransition(HandlingMode src, HandlingMode dst) {
        if (dst == src) return;
        boolean allowed = switch (src) {
            case AI -> dst == HandlingMode.HUMAN || dst == HandlingMode.WAITING_HUMAN;
            case HUMAN -> dst == HandlingMode.AI;
            case WAITING_HUMAN -> dst == HandlingMode.HUMAN || dst == HandlingMode.AI;
        };
        if (!allowed) {
            throw ApiExceptionFactory.invalidTransition("handling_mode", src.value, dst.value);
        }
    }

    /**
     * 转换后不变量（对齐 _check_invariants）：
     *  - resolved 会话必须 handling_mode=ai
     *  - human 模式必须已分配坐席
     */
    private static void checkInvariants(ConvStatus status, HandlingMode mode, String assignedAgentId) {
        if (status == ConvStatus.RESOLVED && mode != HandlingMode.AI) {
            throw new com.agent.cs.common.ApiException(
                    org.springframework.http.HttpStatus.UNPROCESSABLE_ENTITY,
                    "Resolved conversations must have handling_mode=ai",
                    "当前状态不允许此操作");
        }
        if (mode == HandlingMode.HUMAN && (assignedAgentId == null || assignedAgentId.isBlank())) {
            throw new com.agent.cs.common.ApiException(
                    org.springframework.http.HttpStatus.UNPROCESSABLE_ENTITY,
                    "handling_mode=human requires an assigned agent",
                    "当前状态不允许此操作");
        }
    }
}
