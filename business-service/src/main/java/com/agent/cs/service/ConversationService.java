package com.agent.cs.service;

import com.agent.cs.common.ApiException;
import com.agent.cs.domain.Conversation;
import com.agent.cs.domain.Message;
import com.agent.cs.messaging.KafkaEventPublisher;
import com.agent.cs.repository.ConversationRepository;
import com.agent.cs.repository.MessageRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 会话服务：管理面查询（列表/详情）+ 内部消息落库
 *
 * 查询语义对齐 backend/app/api/routes/cs_admin.py，Java 侧成为管理面读源。
 */
@Service
public class ConversationService {

    private static final Logger log = LoggerFactory.getLogger(ConversationService.class);

    private final ConversationRepository conversationRepo;
    private final MessageRepository messageRepo;
    private final KafkaEventPublisher events;

    public ConversationService(ConversationRepository conversationRepo,
                               MessageRepository messageRepo,
                               KafkaEventPublisher events) {
        this.conversationRepo = conversationRepo;
        this.messageRepo = messageRepo;
        this.events = events;
    }

    // ── 管理面查询（对齐 cs_admin.py 响应结构） ──

    public Map<String, Object> listConversations(int limit, String cursor, String status,
                                                 String handlingMode, String userId, String q) {
        OffsetDateTime cursorDt = null;
        if (cursor != null && !cursor.isBlank()) {
            try {
                cursorDt = OffsetDateTime.parse(cursor);
            } catch (Exception e) {
                log.debug("[ConversationService] invalid cursor ignored: {}", cursor);
            }
        }
        String statusF = (status == null || status.isBlank()) ? null : status;
        String modeF = (handlingMode == null || handlingMode.isBlank()) ? null : handlingMode;
        String userF = (userId == null || userId.isBlank()) ? null : userId;
        String qF = (q == null || q.isBlank()) ? null : q.toLowerCase();

        Pageable pageable = PageRequest.of(0, limit + 1);
        List<Conversation> rows = conversationRepo.listPage(statusF, modeF, userF, cursorDt, qF, pageable);
        long total = conversationRepo.countFiltered(statusF, modeF, userF, qF);

        boolean hasMore = rows.size() > limit;
        List<Map<String, Object>> items = rows.stream()
                .limit(limit)
                .map(this::toSummary)
                .toList();

        Map<String, Object> result = new HashMap<>();
        result.put("items", items);
        result.put("total", total);
        result.put("has_more", hasMore);
        return result;
    }

    public Map<String, Object> getConversationDetail(String conversationId) {
        Conversation conv = conversationRepo.findByConversationId(conversationId)
                .orElseThrow(() -> ApiException.notFound("Conversation not found"));

        List<Map<String, Object>> messages = messageRepo.findVisibleByConversationId(conversationId).stream()
                .map(this::toMessageDto)
                .toList();

        Map<String, Object> detail = new HashMap<>();
        detail.put("conversation_id", conv.getConversationId());
        detail.put("user_id", conv.getUserId());
        detail.put("conversation_status", conv.getConversationStatus());
        detail.put("handling_mode", conv.getHandlingMode());
        detail.put("priority", conv.getPriority());
        detail.put("channel", conv.getChannel());
        detail.put("summary", conv.getSummary());
        detail.put("context_summary", conv.getContextSummary());
        detail.put("trace_count", conv.getTraceCount());
        detail.put("last_trace_id", conv.getLastTraceId());
        detail.put("created_at", conv.getCreatedAt() == null ? "" : conv.getCreatedAt().toString());
        detail.put("last_activity_at", conv.getLastActivityAt() == null ? null : conv.getLastActivityAt().toString());
        detail.put("messages", messages);
        return detail;
    }

    public List<String> getTraceIds(String conversationId) {
        return messageRepo.findDistinctTraceIds(conversationId);
    }

    // ── 内部写入（cutover 后由 Java 独占写） ──

    /**
     * get_or_create：按 (conversation_id, user_id) 取会话，不存在则创建。
     * 渠道接入（WhatsApp）与 Python 内部调用共用。
     */
    @Transactional
    public Conversation getOrCreateConversation(String conversationId, String userId, String channel) {
        return conversationRepo.findByConversationId(conversationId).orElseGet(() -> {
            Conversation c = new Conversation();
            c.setConversationId(conversationId);
            c.setUserId(userId);
            c.setChannel(channel == null ? "web" : channel);
            OffsetDateTime now = OffsetDateTime.now();
            c.setCreatedAt(now);
            c.setUpdatedAt(now);
            c.setLastActivityAt(now);
            Conversation saved = conversationRepo.save(c);
            log.info("[ConversationService] created conversation {} for user {}", conversationId, userId);
            return saved;
        });
    }

    @Transactional
    public Message saveMessage(String conversationId, String userId, String senderType, String content,
                               String contentType, String traceId, boolean isPrivate, Map<String, Object> metadata) {
        Conversation conv = getOrCreateConversation(conversationId, userId, null);

        Message m = new Message();
        m.setMessageId(UUID.randomUUID().toString());
        m.setConversation(conv);
        m.setSenderType(senderType == null ? "user" : senderType);
        m.setContent(content);
        m.setContentType(contentType == null ? "text" : contentType);
        m.setTraceId(traceId);
        m.setPrivate(isPrivate);
        if (metadata != null) {
            m.setMetadata(metadata);
        }
        Message saved = messageRepo.save(m);

        // 更新会话活动时间 + 首次回复时间
        OffsetDateTime now = OffsetDateTime.now();
        conv.setLastActivityAt(now);
        conv.setUpdatedAt(now);
        if ("assistant".equals(senderType) && conv.getFirstReplyAt() == null) {
            conv.setFirstReplyAt(now);
        }
        conversationRepo.save(conv);

        // 发布消息事件（fire-and-forget）
        Map<String, Object> payload = new HashMap<>();
        payload.put("message_id", saved.getMessageId());
        payload.put("sender_type", saved.getSenderType());
        payload.put("content_type", saved.getContentType());
        payload.put("trace_id", traceId);
        try {
            events.publishMessageEvent(conversationId, conv.getUserId(), payload);
        } catch (Exception e) {
            log.warn("[ConversationService] event publish failed: {}", e.getMessage());
        }
        return saved;
    }

    // ── DTO 映射 ──

    private Map<String, Object> toSummary(Conversation c) {
        long msgCount = messageRepo.countByConversationId(c.getConversationId());
        Map<String, Object> item = new HashMap<>();
        item.put("conversation_id", c.getConversationId());
        item.put("user_id", c.getUserId());
        item.put("conversation_status", c.getConversationStatus());
        item.put("handling_mode", c.getHandlingMode());
        item.put("priority", c.getPriority());
        item.put("message_count", msgCount);
        item.put("trace_count", c.getTraceCount() == null ? 0 : c.getTraceCount());
        item.put("last_trace_id", c.getLastTraceId());
        item.put("last_activity_at", c.getLastActivityAt() == null ? null : c.getLastActivityAt().toString());
        item.put("created_at", c.getCreatedAt() == null ? "" : c.getCreatedAt().toString());
        item.put("summary", c.getSummary());
        return item;
    }

    private Map<String, Object> toMessageDto(Message m) {
        Map<String, Object> dto = new HashMap<>();
        dto.put("message_id", m.getMessageId());
        dto.put("sender_type", m.getSenderType());
        dto.put("content", m.getContent());
        dto.put("content_type", m.getContentType());
        dto.put("intent_domain", m.getIntentDomain());
        dto.put("intent_name", m.getIntentName());
        dto.put("confidence", m.getConfidence());
        dto.put("trace_id", m.getTraceId());
        dto.put("created_at", m.getCreatedAt() == null ? "" : m.getCreatedAt().toString());
        return dto;
    }
}
