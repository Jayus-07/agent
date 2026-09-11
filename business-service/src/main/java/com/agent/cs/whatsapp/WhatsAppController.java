package com.agent.cs.whatsapp;

import com.agent.cs.config.AppProperties;
import com.agent.cs.messaging.KafkaEventPublisher;
import com.agent.cs.service.ConversationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * WhatsApp Cloud API webhook（Meta 官方接入协议）
 *
 *  - GET  验证：hub.mode=subscribe & hub.verify_token 匹配 → 返回 hub.challenge
 *  - POST 收消息：X-Hub-Signature-256 校验 → 归一化 → 落库（channel=whatsapp）→ Kafka
 *
 * 消息归一化契约（Kafka payload）:
 * { "channel": "whatsapp", "external_user_id": "86138...", "message_id": "wamid.xxx",
 *   "text": "...", "msg_type": "text", "timestamp": "...", "conversation_id": "wa:86138..." }
 */
@RestController
@RequestMapping("/channels/whatsapp")
public class WhatsAppController {

    private static final Logger log = LoggerFactory.getLogger(WhatsAppController.class);

    private final AppProperties props;
    private final ConversationService conversationService;
    private final KafkaEventPublisher events;

    public WhatsAppController(AppProperties props,
                              ConversationService conversationService,
                              KafkaEventPublisher events) {
        this.props = props;
        this.conversationService = conversationService;
        this.events = events;
    }

    /** Meta webhook 订阅验证 */
    @GetMapping("/webhook")
    public ResponseEntity<String> verify(
            @RequestParam(value = "hub.mode", required = false) String hubMode,
            @RequestParam(value = "hub.verify_token", required = false) String hubVerifyToken,
            @RequestParam(value = "hub.challenge", required = false) String hubChallenge) {
        if ("subscribe".equals(hubMode)
                && hubVerifyToken != null
                && hubVerifyToken.equals(props.getWhatsapp().getVerifyToken())) {
            log.info("[WhatsApp] webhook verified");
            return ResponseEntity.ok(hubChallenge == null ? "" : hubChallenge);
        }
        return ResponseEntity.status(403).body("verification failed");
    }

    /** Meta 消息推送 */
    @PostMapping("/webhook")
    public ResponseEntity<Map<String, Object>> receive(
            @RequestHeader(value = "X-Hub-Signature-256", required = false) String signature,
            @RequestBody String rawBody) {

        if (!WhatsAppSignatureVerifier.verify(props.getWhatsapp().getAppSecret(), signature, rawBody)) {
            log.warn("[WhatsApp] invalid signature, rejected");
            return ResponseEntity.status(401).body(Map.of("detail", "invalid signature"));
        }

        try {
            int count = processWebhookPayload(rawBody);
            // Meta 要求 200 快速响应，否则会重试
            return ResponseEntity.ok(Map.of("received", count));
        } catch (Exception e) {
            // 解析失败也返回 200，避免 Meta 无限重试；错误进日志排查
            log.error("[WhatsApp] webhook processing failed: {}", e.getMessage(), e);
            return ResponseEntity.ok(Map.of("received", 0));
        }
    }

    @SuppressWarnings("unchecked")
    private int processWebhookPayload(String rawBody) {
        Map<String, Object> payload = parseJson(rawBody);
        if (payload == null) return 0;

        List<Map<String, Object>> entries = (List<Map<String, Object>>) payload.get("entry");
        if (entries == null) return 0;

        int count = 0;
        for (Map<String, Object> entry : entries) {
            List<Map<String, Object>> changes = (List<Map<String, Object>>) entry.get("changes");
            if (changes == null) continue;
            for (Map<String, Object> change : changes) {
                Map<String, Object> value = (Map<String, Object>) change.get("value");
                if (value == null || !value.containsKey("messages")) continue;

                List<Map<String, Object>> messages = (List<Map<String, Object>>) value.get("messages");
                for (Map<String, Object> msg : messages) {
                    normalizeAndStore(msg);
                    count++;
                }
            }
        }
        return count;
    }

    @SuppressWarnings("unchecked")
    private void normalizeAndStore(Map<String, Object> msg) {
        String fromPhone = String.valueOf(msg.get("from"));           // E.164 不带 +
        String msgType = String.valueOf(msg.getOrDefault("type", "text"));
        String waMessageId = String.valueOf(msg.getOrDefault("id", ""));
        String timestamp = String.valueOf(msg.getOrDefault("timestamp", ""));

        String text = "";
        if ("text".equals(msgType) && msg.get("text") instanceof Map<?, ?> t) {
            text = String.valueOf(((Map<String, Object>) t).getOrDefault("body", ""));
        } else {
            text = "[unsupported type: " + msgType + "]";
        }

        // conversation_id 约定：wa:<phone>（每联系人一个会话）
        String conversationId = "wa:" + fromPhone;
        String userId = "wa:" + fromPhone;

        // 1. 落库（channel=whatsapp，get_or_create 会话）
        conversationService.saveMessage(conversationId, userId, "user", text, "text", null, false,
                Map.of("channel", "whatsapp", "wa_message_id", waMessageId));

        // 2. 发布到 Kafka，供 AI 系统消费生成回复
        events.publishWhatsappInbound(conversationId, userId, Map.of(
                "channel", "whatsapp",
                "external_user_id", fromPhone,
                "message_id", waMessageId,
                "msg_type", msgType,
                "text", text,
                "timestamp", timestamp));
    }

    private Map<String, Object> parseJson(String raw) {
        try {
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            return mapper.readValue(raw, new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {
            });
        } catch (Exception e) {
            log.warn("[WhatsApp] payload parse failed: {}", e.getMessage());
            return null;
        }
    }
}
