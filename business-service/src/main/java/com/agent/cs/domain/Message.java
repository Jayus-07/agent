package com.agent.cs.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.Map;

/**
 * 客服消息 — customer_service.messages
 * 注意：006 迁移遗留了 role NOT NULL 列，新增数据 role=sender_type 以兼容。
 */
@Entity
@Table(name = "messages", schema = "customer_service")
public class Message {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "message_id", nullable = false, unique = true, length = 64)
    private String messageId;

    @ManyToOne
    @JoinColumn(name = "conversation_id", referencedColumnName = "conversation_id", nullable = false)
    private Conversation conversation;

    /** 006 遗留列：NOT NULL，写入时与 sender_type 保持一致 */
    @Column(name = "role", nullable = false, length = 20)
    private String role = "user";

    @Column(name = "sender_type", nullable = false, length = 20)
    private String senderType = "user";

    @Column(name = "sender_id", length = 64)
    private String senderId;

    @Column(name = "content", nullable = false, columnDefinition = "text")
    private String content;

    @Column(name = "content_type", nullable = false, length = 20)
    private String contentType = "text";

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "attachments", columnDefinition = "jsonb")
    private Map<String, Object> attachments;

    @Column(name = "intent_domain", length = 30)
    private String intentDomain;

    @Column(name = "intent_name", length = 50)
    private String intentName;

    @Column(name = "confidence")
    private Double confidence;

    @Column(name = "trace_id", length = 64)
    private String traceId;

    @Column(name = "private", nullable = false)
    private boolean isPrivate = false;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "metadata", columnDefinition = "jsonb", nullable = false)
    private Map<String, Object> metadata = new HashMap<>();

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @PrePersist
    void prePersist() {
        // 兼容 006 遗留 role 列
        this.role = this.senderType;
    }

    // ── getters / setters ──

    public Long getId() { return id; }
    public String getMessageId() { return messageId; }
    public void setMessageId(String messageId) { this.messageId = messageId; }
    public Conversation getConversation() { return conversation; }
    public void setConversation(Conversation conversation) { this.conversation = conversation; }
    public String getRole() { return role; }
    public void setRole(String role) { this.role = role; }
    public String getSenderType() { return senderType; }
    public void setSenderType(String senderType) { this.senderType = senderType; }
    public String getSenderId() { return senderId; }
    public void setSenderId(String senderId) { this.senderId = senderId; }
    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }
    public String getContentType() { return contentType; }
    public void setContentType(String contentType) { this.contentType = contentType; }
    public Map<String, Object> getAttachments() { return attachments; }
    public void setAttachments(Map<String, Object> attachments) { this.attachments = attachments; }
    public String getIntentDomain() { return intentDomain; }
    public void setIntentDomain(String intentDomain) { this.intentDomain = intentDomain; }
    public String getIntentName() { return intentName; }
    public void setIntentName(String intentName) { this.intentName = intentName; }
    public Double getConfidence() { return confidence; }
    public void setConfidence(Double confidence) { this.confidence = confidence; }
    public String getTraceId() { return traceId; }
    public void setTraceId(String traceId) { this.traceId = traceId; }
    public boolean getIsPrivate() { return isPrivate; }
    public void setPrivate(boolean isPrivate) { this.isPrivate = isPrivate; }
    public Map<String, Object> getMetadata() { return metadata; }
    public void setMetadata(Map<String, Object> metadata) { this.metadata = metadata; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
}
