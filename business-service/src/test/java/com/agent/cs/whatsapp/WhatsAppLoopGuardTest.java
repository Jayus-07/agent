package com.agent.cs.whatsapp;

import com.agent.cs.config.AppProperties;
import com.agent.cs.messaging.KafkaEventPublisher;
import com.agent.cs.service.ConversationService;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

/**
 * WhatsApp 回环防护回归测试。
 *
 * Meta webhook 的 payload 中：
 *   - 用户消息在 entry[].changes[].value.messages[]（必须处理）
 *   - 商家已发消息的回执在 entry[].changes[].value.statuses[]（必须忽略，
 *     否则 AI 自己发出的消息会再次触发 AI → 死循环刷屏）
 *
 * WhatsAppController 只处理 containsKey("messages") 的 value，
 * 本测试锁定该行为。
 */
class WhatsAppLoopGuardTest {

    private WhatsAppController newController(ConversationService convSvc, KafkaEventPublisher events) {
        AppProperties props = new AppProperties();
        // appSecret 留空 → 签名校验跳过（本地开发模式），测试聚焦消息路由逻辑
        return new WhatsAppController(props, convSvc, events);
    }

    private static String webhookBody(String valueJson) {
        return """
                {"object":"whatsapp_business_account",
                 "entry":[{"id":"1","changes":[{"field":"messages","value":%s}]}]}
                """.formatted(valueJson);
    }

    @Test
    void processesUserMessage() {
        ConversationService convSvc = mock(ConversationService.class);
        KafkaEventPublisher events = mock(KafkaEventPublisher.class);
        WhatsAppController controller = newController(convSvc, events);

        String body = webhookBody("""
                {"messaging_product":"whatsapp","contacts":[],"messages":[
                  {"from":"8613800000000","id":"wamid.user1","type":"text",
                   "text":{"body":"订单到哪了"},"timestamp":"1726118400"}]}
                """);

        ResponseEntity<Map<String, Object>> resp = controller.receive(null, body);

        assertEquals(200, resp.getStatusCode().value());
        assertEquals(1, resp.getBody().get("received"));
        verify(convSvc).saveMessage(eq("wa:8613800000000"), eq("wa:8613800000000"),
                eq("user"), eq("订单到哪了"), eq("text"), eq(null), eq(false), any());
        verify(events).publishWhatsappInbound(eq("wa:8613800000000"),
                eq("wa:8613800000000"), any());
    }

    @Test
    void ignoresStatusesOnlyPayload() {
        /** statuses（AI 自己发出消息的回执）不触发任何落库/事件 → 回环被切断 */
        ConversationService convSvc = mock(ConversationService.class);
        KafkaEventPublisher events = mock(KafkaEventPublisher.class);
        WhatsAppController controller = newController(convSvc, events);

        String body = webhookBody("""
                {"messaging_product":"whatsapp","statuses":[
                  {"id":"wamid.ai1","status":"delivered","recipient_id":"8613800000000"}]}
                """);

        ResponseEntity<Map<String, Object>> resp = controller.receive(null, body);

        assertEquals(200, resp.getStatusCode().value());
        assertEquals(0, resp.getBody().get("received"));
        verifyNoInteractions(convSvc, events);
    }

    @Test
    void mixedPayloadOnlyProcessesMessages() {
        /** 同一 value 同时带 statuses 与 messages：只处理 messages */
        ConversationService convSvc = mock(ConversationService.class);
        KafkaEventPublisher events = mock(KafkaEventPublisher.class);
        WhatsAppController controller = newController(convSvc, events);

        String body = webhookBody("""
                {"messaging_product":"whatsapp",
                 "statuses":[{"id":"wamid.ai2","status":"read"}],
                 "messages":[{"from":"8613900000000","id":"wamid.user2","type":"text",
                              "text":{"body":"hi"},"timestamp":"1726118401"}]}
                """);

        controller.receive(null, body);

        verify(convSvc).saveMessage(eq("wa:8613900000000"), eq("wa:8613900000000"),
                eq("user"), eq("hi"), eq("text"), eq(null), eq(false), any());
    }
}
