package com.agent.cs.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 客服会话 — customer_service.conversations
 * 双维度状态：conversation_status (open/pending/resolved/snoozed) × handling_mode (ai/human/waiting_human)
 * 对齐 Python CSConversation（models/conversation.py）与 006+0003 迁移。
 */
@Entity
@Table(name = "conversations", schema = "customer_service")
public class Conversation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "conversation_id", nullable = false, unique = true, length = 64)
    private String conversationId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "conversation_status", nullable = false, length = 20)
    private String conversationStatus = "open";

    @Column(name = "handling_mode", nullable = false, length = 20)
    private String handlingMode = "ai";

    @Column(name = "channel", nullable = false, length = 20)
    private String channel = "web";

    @Column(name = "inbox_id", length = 64)
    private String inboxId;

    @Column(name = "assigned_agent_id", length = 64)
    private String assignedAgentId;

    @Column(name = "team_id", length = 64)
    private String teamId;

    @Column(name = "priority", nullable = false, length = 10)
    private String priority = "medium";

    @JdbcTypeCode(SqlTypes.ARRAY)
    @Column(name = "labels", columnDefinition = "text[]", nullable = false)
    private List<String> labels = new ArrayList<>();

    @Column(name = "ai_enabled", nullable = false)
    private boolean aiEnabled = true;

    @Column(name = "summary", columnDefinition = "text")
    private String summary;

    @Column(name = "context_summary", columnDefinition = "text")
    private String contextSummary;

    @Column(name = "last_trace_id", length = 64)
    private String lastTraceId;

    @Column(name = "trace_count", nullable = false)
    private Long traceCount = 0L;

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @Column(name = "closed_at")
    private OffsetDateTime closedAt;

    @Column(name = "first_reply_at")
    private OffsetDateTime firstReplyAt;

    @Column(name = "last_activity_at")
    private OffsetDateTime lastActivityAt;

    @OneToMany(mappedBy = "conversation", fetch = FetchType.LAZY)
    private List<Message> messages = new ArrayList<>();

    public boolean isOpen() { return "open".equals(conversationStatus); }
    public boolean isResolved() { return "resolved".equals(conversationStatus); }
    public boolean isAiHandling() { return "ai".equals(handlingMode); }

    // ── getters / setters ──

    public Long getId() { return id; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getConversationStatus() { return conversationStatus; }
    public void setConversationStatus(String conversationStatus) { this.conversationStatus = conversationStatus; }
    public String getHandlingMode() { return handlingMode; }
    public void setHandlingMode(String handlingMode) { this.handlingMode = handlingMode; }
    public String getChannel() { return channel; }
    public void setChannel(String channel) { this.channel = channel; }
    public String getInboxId() { return inboxId; }
    public void setInboxId(String inboxId) { this.inboxId = inboxId; }
    public String getAssignedAgentId() { return assignedAgentId; }
    public void setAssignedAgentId(String assignedAgentId) { this.assignedAgentId = assignedAgentId; }
    public String getTeamId() { return teamId; }
    public void setTeamId(String teamId) { this.teamId = teamId; }
    public String getPriority() { return priority; }
    public void setPriority(String priority) { this.priority = priority; }
    public List<String> getLabels() { return labels; }
    public void setLabels(List<String> labels) { this.labels = labels; }
    public boolean isAiEnabled() { return aiEnabled; }
    public void setAiEnabled(boolean aiEnabled) { this.aiEnabled = aiEnabled; }
    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }
    public String getContextSummary() { return contextSummary; }
    public void setContextSummary(String contextSummary) { this.contextSummary = contextSummary; }
    public String getLastTraceId() { return lastTraceId; }
    public void setLastTraceId(String lastTraceId) { this.lastTraceId = lastTraceId; }
    public Long getTraceCount() { return traceCount; }
    public void setTraceCount(Long traceCount) { this.traceCount = traceCount; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
    public OffsetDateTime getClosedAt() { return closedAt; }
    public void setClosedAt(OffsetDateTime closedAt) { this.closedAt = closedAt; }
    public OffsetDateTime getFirstReplyAt() { return firstReplyAt; }
    public void setFirstReplyAt(OffsetDateTime firstReplyAt) { this.firstReplyAt = firstReplyAt; }
    public OffsetDateTime getLastActivityAt() { return lastActivityAt; }
    public void setLastActivityAt(OffsetDateTime lastActivityAt) { this.lastActivityAt = lastActivityAt; }
    public List<Message> getMessages() { return messages; }
    public void setMessages(List<Message> messages) { this.messages = messages; }
}
