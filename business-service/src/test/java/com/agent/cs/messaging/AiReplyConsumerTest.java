package com.agent.cs.messaging;

import com.agent.cs.service.AuditService;
import com.agent.cs.whatsapp.WhatsAppSender;
import org.junit.jupiter.api.Test;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * AI 回复消费者行为测试：幂等去重、渠道过滤、发送与审计
 */
class AiReplyConsumerTest {

    private static String replyEvent(String eventId, String channel, String text) {
        return """
                {"event_id":"%s","event_type":"ai.reply.created",
                 "occurred_at":"2026-09-12T08:00:00Z","source":"ai-service",
                 "conversation_id":"wa:8613800000000","user_id":"8613800000000",
                 "payload":{"channel":"%s","conversation_id":"wa:8613800000000",
                            "external_user_id":"8613800000000",
                            "reply_to_message_id":"wamid.user1","text":"%s"}}
                """.formatted(eventId, channel, text);
    }

    @Test
    void sendsReplyAndRecordsAudit() {
        WhatsAppSender sender = mock(WhatsAppSender.class);
        AuditService audit = mock(AuditService.class);
        when(sender.sendText(anyString(), anyString())).thenReturn(true);

        AiReplyConsumer consumer = new AiReplyConsumer(sender, audit);
        consumer.onAiReply(replyEvent("evt-1", "whatsapp", "您好，已为您查询"));

        verify(sender).sendText("8613800000000", "您好，已为您查询");
        verify(audit).record(eq("8613800000000"), eq("whatsapp.reply.sent"),
                eq("success"), eq("message"), eq("wamid.user1"),
                any(), eq("wa:8613800000000"), anyString(), anyString(),
                any(), any());
    }

    @Test
    void duplicateEventSkipped() {
        WhatsAppSender sender = mock(WhatsAppSender.class);
        AuditService audit = mock(AuditService.class);

        AiReplyConsumer consumer = new AiReplyConsumer(sender, audit);
        consumer.onAiReply(replyEvent("evt-dup", "whatsapp", "重复消息"));
        consumer.onAiReply(replyEvent("evt-dup", "whatsapp", "重复消息"));

        verify(sender, org.mockito.Mockito.times(1)).sendText(anyString(), anyString());
        // 仅第一条事件产生一次审计
        verify(audit, org.mockito.Mockito.times(1)).record(anyString(), anyString(),
                anyString(), any(), any(), any(), anyString(), anyString(),
                anyString(), any(), any());
    }

    @Test
    void nonWhatsappChannelDropped() {
        WhatsAppSender sender = mock(WhatsAppSender.class);
        AuditService audit = mock(AuditService.class);

        AiReplyConsumer consumer = new AiReplyConsumer(sender, audit);
        consumer.onAiReply(replyEvent("evt-2", "web", "网页回复"));

        verifyNoInteractions(sender, audit);
    }

    @Test
    void unparsablePayloadDoesNotThrow() {
        WhatsAppSender sender = mock(WhatsAppSender.class);
        AuditService audit = mock(AuditService.class);

        AiReplyConsumer consumer = new AiReplyConsumer(sender, audit);
        consumer.onAiReply("not-json{{");

        verifyNoInteractions(sender, audit);
    }
}
