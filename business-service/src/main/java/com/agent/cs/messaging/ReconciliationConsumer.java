package com.agent.cs.messaging;

import com.agent.cs.config.KafkaTopicsConfig;
import com.agent.cs.domain.Conversation;
import com.agent.cs.repository.ConversationRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;


/**
 * Kafka 幂等对账消费者（cutover 双写窗口期数据安全网）。
 *
 * 职责（docs/architecture-overview.md §6 第 4 步）：
 *  1. 幂等去重：按 event_id（LRU 上限内）丢弃重复事件——
 *     双写窗口期 Python/Java 各自发布，同一会话状态可能被重复投递
 *  2. 对账告警：conversation.state_changed 携带的状态与本地库不一致时
 *     记 WARNING（仅告警，不覆盖写——写权归唯一 writer）
 *
 * value 用 StringDeserializer 接原始 JSON：Java JsonSerializer 与 Python
 * kafka-python 两种生产者的载荷都能按同一契约解析（docs/contracts/events.schema.json）。
 */
@Component
public class ReconciliationConsumer {

    private static final Logger log = LoggerFactory.getLogger(ReconciliationConsumer.class);

    /** 去重缓存（按 event_id；超过后淘汰最旧——对账窗口有限，无需持久化） */
    private static final int DEDUP_MAX = 100_000;

    private final ObjectMapper mapper = new ObjectMapper();
    private final ConversationRepository conversationRepository;
    private final EventDedupCache dedupCache = new EventDedupCache(DEDUP_MAX);

    public ReconciliationConsumer(ConversationRepository conversationRepository) {
        this.conversationRepository = conversationRepository;
    }

    @KafkaListener(topics = {KafkaTopicsConfig.TOPIC_CONVERSATION, KafkaTopicsConfig.TOPIC_MESSAGE},
                   groupId = "business-service")
    public void onEvent(String raw) {
        JsonNode event;
        try {
            event = mapper.readTree(raw);
        } catch (Exception e) {
            log.warn("[Reconcile] unparsable event dropped: {}", e.getMessage());
            return;
        }

        String eventId = event.path("event_id").asText("");
        String eventType = event.path("event_type").asText("");
        String conversationId = event.path("conversation_id").asText("");
        if (eventId.isEmpty()) {
            log.warn("[Reconcile] event without event_id dropped: {}", eventType);
            return;
        }

        // 1. 幂等去重：重复投递（双方双写/重试）只记 debug
        if (!dedupCache.firstSeen(eventId)) {
            log.debug("[Reconcile] duplicate event skipped: {} {}", eventType, eventId);
            return;
        }

        // 2. 对账：conversation 状态事件与本地库比对（仅告警）
        if (KafkaEventPublisher.EVT_STATUS_CHANGED.equals(eventType) && !conversationId.isEmpty()) {
            reconcileConversationStatus(conversationId, event.path("payload"));
        }
    }

    private void reconcileConversationStatus(String conversationId, JsonNode payload) {
        try {
            String remoteStatus = payload.path("conversation_status").asText("");
            if (remoteStatus.isEmpty()) {
                return;
            }
            conversationRepository.findByConversationId(conversationId).ifPresentOrElse(
                    conv -> {
                        if (!remoteStatus.equals(conv.getConversationStatus())) {
                            log.warn("[Reconcile] status mismatch for {}: local={}, event={}",
                                    conversationId, conv.getConversationStatus(), remoteStatus);
                        }
                    },
                    () -> log.warn("[Reconcile] unknown conversation in event: {}", conversationId)
            );
        } catch (Exception e) {
            // 对账是旁路，任何失败不影响消费位点
            log.warn("[Reconcile] reconciliation failed for {}: {}", conversationId, e.getMessage());
        }
    }
}
