package com.agent.cs.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.Map;

/**
 * 客服动作记录 — customer_service.agent_actions
 */
@Entity
@Table(name = "agent_actions", schema = "customer_service")
public class AgentAction {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "action_id", nullable = false, unique = true, length = 64)
    private String actionId;

    @Column(name = "conversation_id", nullable = false, length = 64)
    private String conversationId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "action_type", nullable = false, length = 30)
    private String actionType;

    @Column(name = "target_type", length = 30)
    private String targetType;

    @Column(name = "target_id", length = 64)
    private String targetId;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "before_state", columnDefinition = "jsonb")
    private Map<String, Object> beforeState;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "after_state", columnDefinition = "jsonb")
    private Map<String, Object> afterState;

    @Column(name = "confirmation_state", nullable = false, length = 20)
    private String confirmationState = "not_required";

    @Column(name = "status", nullable = false, length = 20)
    private String status = "pending";

    @Column(name = "executed_by", nullable = false, length = 20)
    private String executedBy = "ai";

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "executed_at")
    private OffsetDateTime executedAt;

    // ── getters / setters ──

    public Long getId() { return id; }
    public String getActionId() { return actionId; }
    public void setActionId(String actionId) { this.actionId = actionId; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getActionType() { return actionType; }
    public void setActionType(String actionType) { this.actionType = actionType; }
    public String getTargetType() { return targetType; }
    public void setTargetType(String targetType) { this.targetType = targetType; }
    public String getTargetId() { return targetId; }
    public void setTargetId(String targetId) { this.targetId = targetId; }
    public Map<String, Object> getBeforeState() { return beforeState; }
    public void setBeforeState(Map<String, Object> beforeState) { this.beforeState = beforeState; }
    public Map<String, Object> getAfterState() { return afterState; }
    public void setAfterState(Map<String, Object> afterState) { this.afterState = afterState; }
    public String getConfirmationState() { return confirmationState; }
    public void setConfirmationState(String confirmationState) { this.confirmationState = confirmationState; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getExecutedBy() { return executedBy; }
    public void setExecutedBy(String executedBy) { this.executedBy = executedBy; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
    public OffsetDateTime getExecutedAt() { return executedAt; }
    public void setExecutedAt(OffsetDateTime executedAt) { this.executedAt = executedAt; }
}
