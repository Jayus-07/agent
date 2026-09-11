package com.agent.cs.service;

import com.agent.cs.common.ApiException;
import com.agent.cs.config.KafkaTopicsConfig;
import com.agent.cs.domain.Confirmation;
import com.agent.cs.domain.Conversation;
import com.agent.cs.domain.Handoff;
import com.agent.cs.messaging.KafkaEventPublisher;
import com.agent.cs.repository.ConfirmationRepository;
import com.agent.cs.repository.ConversationRepository;
import com.agent.cs.repository.HandoffRepository;
import com.agent.cs.statemachine.ConfirmationStateMachine;
import com.agent.cs.statemachine.ConversationStateMachine;
import com.agent.cs.statemachine.HandoffStateMachine;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 统一状态转换服务（直译自 backend/customer_service/state_transition.py）
 *
 * 所有 CS 状态变更（confirmation / handoff / conversation）的唯一入口：
 *  1. 接收 StateTransitionRequest
 *  2. 逐维度校验（调用各状态机）
 *  3. 写入 PostgreSQL（通过 Repository）
 *  4. 发布 Kafka 事件
 *  5. 返回 StateTransitionResult 快照
 */
@Service
public class StateTransitionService {

    private static final Logger log = LoggerFactory.getLogger(StateTransitionService.class);

    private final ConversationRepository conversationRepo;
    private final ConfirmationRepository confirmationRepo;
    private final HandoffRepository handoffRepo;
    private final KafkaEventPublisher events;

    public StateTransitionService(ConversationRepository conversationRepo,
                                  ConfirmationRepository confirmationRepo,
                                  HandoffRepository handoffRepo,
                                  KafkaEventPublisher events) {
        this.conversationRepo = conversationRepo;
        this.confirmationRepo = confirmationRepo;
        this.handoffRepo = handoffRepo;
        this.events = events;
    }

    /** 请求体（对齐 StateTransitionRequest：所有字段可选，user_id + session_id + conversation_id 为定位键） */
    public record StateTransitionRequest(
            String userId,
            String sessionId,
            String conversationId,
            String confirmationTarget,
            String handoffTarget,
            String conversationStatusTarget,
            String handlingModeTarget,
            Map<String, Object> pendingAction,
            String assignedAgentId
    ) {
    }

    /** 结果快照（对齐 StateTransitionResult） */
    public record StateTransitionResult(
            boolean success,
            String confirmationState,
            String handoffState,
            String conversationStatus,
            String handlingMode,
            Map<String, Object> pendingAction,
            List<String> errors
    ) {
    }

    @Transactional
    public StateTransitionResult apply(StateTransitionRequest request) {
        List<String> errors = new ArrayList<>();
        String confirmationState = "";
        String handoffState = "";
        String conversationStatus = "";
        String handlingMode = "";

        String userId = request.userId() == null ? "" : request.userId();
        String conversationId = request.conversationId() == null ? request.sessionId() : request.conversationId();

        if (request.confirmationTarget() != null) {
            String[] r = applyConfirmation(userId, conversationId, request.confirmationTarget(), request.pendingAction());
            confirmationState = r[0];
            if (!r[1].isEmpty()) errors.add(r[1]);
        }

        if (request.handoffTarget() != null) {
            String[] r = applyHandoff(userId, conversationId, request.handoffTarget());
            handoffState = r[0];
            if (!r[1].isEmpty()) errors.add(r[1]);
        }

        if (request.conversationStatusTarget() != null || request.handlingModeTarget() != null) {
            String[] r = applyConversation(conversationId, request.conversationStatusTarget(),
                    request.handlingModeTarget(), request.assignedAgentId());
            conversationStatus = r[0];
            handlingMode = r[1];
            if (!r[2].isEmpty()) errors.add(r[2]);
        }

        // 对齐 Python：未显式变更的维度回填当前状态
        if (errors.isEmpty()) {
            if (confirmationState.isEmpty()) {
                confirmationState = loadConfirmationState(userId, conversationId);
            }
            if (handoffState.isEmpty()) {
                handoffState = loadHandoffState(userId, conversationId);
            }
            if (conversationStatus.isEmpty() || handlingMode.isEmpty()) {
                Map<String, String> snap = loadConversationSnapshot(conversationId);
                if (snap != null) {
                    conversationStatus = conversationStatus.isEmpty() ? snap.get("conversation_status") : conversationStatus;
                    handlingMode = handlingMode.isEmpty() ? snap.get("handling_mode") : handlingMode;
                }
            }
        }

        Map<String, Object> pendingAction = request.pendingAction();
        return new StateTransitionResult(
                errors.isEmpty(),
                confirmationState,
                handoffState,
                conversationStatus,
                handlingMode,
                pendingAction,
                errors
        );
    }

    // ── confirmation 维度 ──

    private String[] applyConfirmation(String userId, String conversationId, String target,
                                       Map<String, Object> pendingAction) {
        String currentStr = loadConfirmationState(userId, conversationId);
        try {
            ConfirmationStateMachine.ConfirmationState current =
                    ConfirmationStateMachine.ConfirmationState.of(currentStr);
            ConfirmationStateMachine.ConfirmationState targetEnum =
                    ConfirmationStateMachine.ConfirmationState.of(target);
            ConfirmationStateMachine.transition(current, targetEnum);
        } catch (Exception e) {
            return new String[]{currentStr, String.valueOf(e.getMessage())};
        }

        Confirmation existing = confirmationRepo.findPending(userId, conversationId).orElse(null);
        if (existing != null) {
            OffsetDateTime now = OffsetDateTime.now();
            switch (target) {
                case "confirmed" -> confirmationRepo.updateState(existing.getConfirmationId(), target, now, null);
                case "executing", "success", "failed" ->
                        confirmationRepo.updateState(existing.getConfirmationId(), target, null, now);
                default -> confirmationRepo.updateState(existing.getConfirmationId(), target, null, null);
            }
        } else if (pendingAction != null) {
            savePendingConfirmation(userId, conversationId, target, pendingAction);
        }

        publishSafe(() -> events.publishConversationEvent(
                KafkaEventPublisher.EVT_CONFIRMATION_STATE_CHANGED, conversationId, userId,
                Map.of("confirmation_state", target)));
        return new String[]{target, ""};
    }

    private void savePendingConfirmation(String userId, String conversationId, String state,
                                         Map<String, Object> pendingAction) {
        Confirmation c = new Confirmation();
        c.setConfirmationId(String.valueOf(pendingAction.getOrDefault("action_id", UUID.randomUUID().toString())));
        c.setConversationId(conversationId);
        c.setUserId(userId);
        c.setActionType(String.valueOf(pendingAction.getOrDefault("action_type", "")));
        c.setTargetType(String.valueOf(pendingAction.getOrDefault("target_type", "")));
        c.setTargetId(String.valueOf(pendingAction.getOrDefault("target_id", "")));
        c.setProposal(new LinkedHashMap<>(pendingAction));
        c.setState(state);
        Object expiresAt = pendingAction.get("expires_at");
        c.setExpiresAt(expiresAt != null
                ? OffsetDateTime.parse(expiresAt.toString())
                : OffsetDateTime.now().plusSeconds(300));
        confirmationRepo.save(c);
    }

    // ── handoff 维度 ──

    private String[] applyHandoff(String userId, String conversationId, String target) {
        String currentStr = loadHandoffState(userId, conversationId);
        try {
            HandoffStateMachine.HandoffState current = HandoffStateMachine.HandoffState.of(currentStr);
            HandoffStateMachine.HandoffState targetEnum = HandoffStateMachine.HandoffState.of(target);
            HandoffStateMachine.TransitionResult result = HandoffStateMachine.transition(current, targetEnum);
            if (result.changed()) {
                Handoff existing = handoffRepo.findActive(userId, conversationId).orElse(null);
                if (existing != null) {
                    OffsetDateTime closedAt = "closed".equals(target) ? OffsetDateTime.now() : null;
                    handoffRepo.updateState(existing.getHandoffId(), target, closedAt, OffsetDateTime.now());
                } else {
                    Handoff h = new Handoff();
                    h.setHandoffId(UUID.randomUUID().toString());
                    h.setConversationId(conversationId);
                    h.setUserId(userId);
                    h.setHandoffState(target);
                    handoffRepo.save(h);
                }
            }
        } catch (Exception e) {
            return new String[]{currentStr, String.valueOf(e.getMessage())};
        }

        publishSafe(() -> events.publishHandoffEvent(
                KafkaEventPublisher.EVT_HANDOFF_STATE_CHANGED, conversationId, userId,
                Map.of("handoff_state", target)));
        return new String[]{target, ""};
    }

    // ── conversation 维度 ──

    private String[] applyConversation(String conversationId, String statusTarget, String modeTarget,
                                       String assignedAgentId) {
        Conversation conv = conversationRepo.findByConversationId(conversationId).orElse(null);
        if (conv == null) {
            return new String[]{"", "", "Conversation " + conversationId + " not found"};
        }
        try {
            ConversationStateMachine.TransitionResult result = ConversationStateMachine.transition(
                    conv.getConversationStatus(), conv.getHandlingMode(),
                    statusTarget, modeTarget,
                    assignedAgentId != null ? assignedAgentId : conv.getAssignedAgentId());
            if (result.changed()) {
                conv.setConversationStatus(result.statusValue());
                conv.setHandlingMode(result.modeValue());
                if (assignedAgentId != null) {
                    conv.setAssignedAgentId(assignedAgentId);
                }
                if ("resolved".equals(result.statusValue())) {
                    conv.setClosedAt(OffsetDateTime.now());
                }
                conv.setUpdatedAt(OffsetDateTime.now());
                conversationRepo.save(conv);
            }
            publishSafe(() -> events.publishConversationEvent(
                    KafkaEventPublisher.EVT_STATUS_CHANGED, conversationId, conv.getUserId(),
                    Map.of("conversation_status", result.statusValue(),
                            "handling_mode", result.modeValue(),
                            "changed", result.changed())));
            return new String[]{result.statusValue(), result.modeValue(), ""};
        } catch (Exception e) {
            return new String[]{conv.getConversationStatus(), conv.getHandlingMode(), String.valueOf(e.getMessage())};
        }
    }

    // ── 快照加载（对齐 load_snapshot） ──

    public Map<String, Object> loadSnapshot(String userId, String sessionId, String conversationId) {
        try {
            String convId = conversationId != null ? conversationId : sessionId;
            Map<String, Object> snap = new HashMap<>();
            snap.put("conversation_status", "open");
            snap.put("handling_mode", "ai");
            snap.put("handoff_state", loadHandoffState(userId, convId));
            snap.put("confirmation_state", loadConfirmationState(userId, convId));

            Map<String, String> conv = loadConversationSnapshot(convId);
            if (conv != null) {
                snap.put("conversation_status", conv.get("conversation_status"));
                snap.put("handling_mode", conv.get("handling_mode"));
            }

            confirmationRepo.findPending(userId, convId).ifPresentOrElse(
                    c -> snap.put("pending_action", c.getProposal()),
                    () -> snap.put("pending_action", null));
            return snap;
        } catch (Exception e) {
            log.warn("[StateTransitionService] load_snapshot failed, returning defaults: {}", e.getMessage());
            return defaultSnapshot();
        }
    }

    private String loadConfirmationState(String userId, String conversationId) {
        return confirmationRepo.findPending(userId, conversationId)
                .map(Confirmation::getState)
                .orElse("not_required");
    }

    private String loadHandoffState(String userId, String conversationId) {
        return handoffRepo.findActive(userId, conversationId)
                .map(Handoff::getHandoffState)
                .orElse("ai_active");
    }

    private Map<String, String> loadConversationSnapshot(String conversationId) {
        return conversationRepo.findByConversationId(conversationId)
                .map(c -> Map.of(
                        "conversation_status", c.getConversationStatus(),
                        "handling_mode", c.getHandlingMode()))
                .orElse(null);
    }

    private static Map<String, Object> defaultSnapshot() {
        Map<String, Object> snap = new HashMap<>();
        snap.put("conversation_status", "open");
        snap.put("handling_mode", "ai");
        snap.put("handoff_state", "ai_active");
        snap.put("confirmation_state", "not_required");
        snap.put("pending_action", null);
        return snap;
    }

    private void publishSafe(Runnable r) {
        try {
            r.run();
        } catch (Exception e) {
            log.warn("[StateTransitionService] event publish failed: {}", e.getMessage());
        }
    }

    /** 供内部 REST 校验会话存在 */
    public Conversation requireConversation(String conversationId) {
        return conversationRepo.findByConversationId(conversationId)
                .orElseThrow(() -> ApiException.notFound("Conversation not found: " + conversationId));
    }
}
