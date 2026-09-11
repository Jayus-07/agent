package com.agent.cs.messaging;

import com.agent.cs.config.KafkaTopicsConfig;
import com.agent.cs.service.AuditService;
import com.agent.cs.whatsapp.WhatsAppSender;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * AI 回复消费者（WhatsApp 闭环最后一环）。
 *
 * 链路：channel.whatsapp.inbound → ai-service 消费 → Agent 生成回复
 *   → ai.reply.events → 本消费者 → WhatsAppSender（Graph API）发送 → 审计
 *
 * 事件契约见 docs/contracts/events.schema.json（ai.reply.created）。
 * 幂等：按 event_id 去重；无 WHATSAPP_ACCESS_TOKEN 时 WhatsAppSender
 * 内部降级为仅日志（与 webhook 收流同款策略）。
 */
@Component
public class AiReplyConsumer {

    private static final Logger log = LoggerFactory.getLogger(AiReplyConsumer.class);

    private static final int DEDUP_MAX = 100_000;

    private final ObjectMapper mapper = new ObjectMapper();
    private final WhatsAppSender sender;
    private final AuditService auditService;
    private final EventDedupCache dedupCache = new EventDedupCache(DEDUP_MAX);

    public AiReplyConsumer(WhatsAppSender sender, AuditService auditService) {
        this.sender = sender;
        this.auditService = auditService;
    }

    @KafkaListener(topics = KafkaTopicsConfig.TOPIC_AI_REPLY, groupId = "business-service")
    public void onAiReply(String raw) {
        JsonNode event;
        try {
            event = mapper.readTree(raw);
        } catch (Exception e) {
            log.warn("[AiReply] unparsable reply event dropped: {}", e.getMessage());
            return;
        }

        String eventId = event.path("event_id").asText("");
        if (!dedupCache.firstSeen(eventId)) {
            log.debug("[AiReply] duplicate reply event skipped: {}", eventId);
            return;
        }

        String eventType = event.path("event_type").asText("");
        if (!"ai.reply.created".equals(eventType)) {
            return;
        }

        JsonNode payload = event.path("payload");
        String channel = payload.path("channel").asText("");
        String text = payload.path("text").asText("");
        String conversationId = payload.path("conversation_id").asText("");
        String externalUserId = payload.path("external_user_id").asText("");
        String replyTo = payload.path("reply_to_message_id").asText("");

        if (text.isEmpty() || externalUserId.isEmpty()) {
            log.warn("[AiReply] reply event missing text/external_user_id, dropped");
            return;
        }

        if (!"whatsapp".equals(channel)) {
            log.warn("[AiReply] unsupported channel '{}' for reply, dropped", channel);
            return;
        }

        boolean sent = sender.sendText(externalUserId, text);
        // 审计：发送结果落库（system 动作，actor=ai-service 侧的回复链路）
        auditService.record(externalUserId, "whatsapp.reply.sent",
                sent ? "success" : "failed", "message", replyTo,
                sent ? null : "sendText returned false (token missing or Graph API error)",
                conversationId, "system", "ai-reply-consumer", null, null);
    }
}
