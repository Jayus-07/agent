package com.agent.cs.messaging;

import com.agent.cs.config.KafkaTopicsConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Kafka 事件发布器 — 业务系统 → AI 系统的事件出口
 *
 * 事件契约（JSON）:
 * {
 *   "event_id":   "...",
 *   "event_type": "conversation.status_changed",
 *   "occurred_at":"2026-09-12T08:00:00Z",
 *   "source":     "business-service",
 *   "conversation_id": "...",
 *   "user_id":    "...",
 *   "payload":    { ...维度相关数据... }
 * }
 *
 * 发布失败只记日志不抛异常（事件是尽力而为的旁路，不阻塞主流程）。
 */
@Component
public class KafkaEventPublisher {

    private static final Logger log = LoggerFactory.getLogger(KafkaEventPublisher.class);

    public static final String EVT_STATUS_CHANGED = "conversation.status_changed";
    public static final String EVT_MESSAGE_CREATED = "message.created";
    public static final String EVT_HANDOFF_STATE_CHANGED = "handoff.state_changed";
    public static final String EVT_HANDOFF_REQUESTED = "handoff.requested";
    public static final String EVT_ACTION_EXECUTED = "action.executed";
    public static final String EVT_CONFIRMATION_STATE_CHANGED = "confirmation.state_changed";
    public static final String EVT_WHATSAPP_INBOUND = "whatsapp.message.inbound";

    private final KafkaTemplate<String, Map<String, Object>> kafkaTemplate;

    public KafkaEventPublisher(KafkaTemplate<String, Map<String, Object>> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publish(String topic, String eventType, String conversationId, String userId,
                        Map<String, Object> payload) {
        try {
            Map<String, Object> event = new HashMap<>();
            event.put("event_id", UUID.randomUUID().toString());
            event.put("event_type", eventType);
            event.put("occurred_at", OffsetDateTime.now().toString());
            event.put("source", "business-service");
            event.put("conversation_id", conversationId);
            event.put("user_id", userId);
            event.put("payload", payload == null ? Map.of() : payload);

            // key = conversation_id，保证同一会话的事件有序
            kafkaTemplate.send(topic, conversationId == null ? UUID.randomUUID().toString() : conversationId, event);
            log.debug("[Kafka] published {} to {}", eventType, topic);
        } catch (Exception e) {
            // fire-and-forget：Kafka 不可用不阻塞业务主流程
            log.warn("[Kafka] publish {} failed: {}", eventType, e.getMessage());
        }
    }

    public void publishConversationEvent(String eventType, String conversationId, String userId,
                                         Map<String, Object> payload) {
        publish(KafkaTopicsConfig.TOPIC_CONVERSATION, eventType, conversationId, userId, payload);
    }

    public void publishMessageEvent(String conversationId, String userId, Map<String, Object> payload) {
        publish(KafkaTopicsConfig.TOPIC_MESSAGE, EVT_MESSAGE_CREATED, conversationId, userId, payload);
    }

    public void publishHandoffEvent(String eventType, String conversationId, String userId,
                                    Map<String, Object> payload) {
        publish(KafkaTopicsConfig.TOPIC_HANDOFF, eventType, conversationId, userId, payload);
    }

    public void publishActionEvent(String conversationId, String userId, Map<String, Object> payload) {
        publish(KafkaTopicsConfig.TOPIC_ACTION, EVT_ACTION_EXECUTED, conversationId, userId, payload);
    }

    public void publishWhatsappInbound(String conversationId, String userId, Map<String, Object> payload) {
        publish(KafkaTopicsConfig.TOPIC_WHATSAPP_INBOUND, EVT_WHATSAPP_INBOUND, conversationId, userId, payload);
    }
}
