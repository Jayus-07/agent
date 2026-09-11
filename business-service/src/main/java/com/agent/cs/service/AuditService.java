package com.agent.cs.service;

import com.agent.cs.domain.AuditLog;
import com.agent.cs.repository.AuditLogRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.UUID;

/**
 * 审计服务（对齐 backend/customer_service/audit.py + audit_logs 表）
 *
 * result ∈ {success, failure, denied, error}
 */
@Service
public class AuditService {

    private static final Logger log = LoggerFactory.getLogger(AuditService.class);

    private final AuditLogRepository auditRepo;

    public AuditService(AuditLogRepository auditRepo) {
        this.auditRepo = auditRepo;
    }

    /**
     * 对齐 build_audit_entry：构建并持久化一条审计日志。
     */
    public AuditLog record(String userId, String actionType, String result,
                           String targetType, String targetId, String detail,
                           String conversationId, String actorType, String actorId,
                           Map<String, Object> beforeState, Map<String, Object> afterState) {
        AuditLog entry = new AuditLog();
        entry.setLogId(UUID.randomUUID().toString());
        entry.setUserId(userId == null ? "" : userId);
        entry.setConversationId(conversationId);
        entry.setActorType(actorType == null ? "system" : actorType);
        entry.setActorId(actorId);
        entry.setAction(actionType);
        entry.setResourceType(targetType);
        entry.setResourceId(targetId);
        entry.setResult(result);
        entry.setErrorDetail(detail);
        try {
            return auditRepo.save(entry);
        } catch (Exception e) {
            // 审计写入失败不阻塞业务
            log.warn("[AuditService] audit write failed: {}", e.getMessage());
            return entry;
        }
    }

    /** 动作执行成功的便捷方法 */
    public void recordSuccess(String userId, String actionType, String conversationId,
                              String targetType, String targetId, String actorType) {
        record(userId, actionType, "success", targetType, targetId, null,
                conversationId, actorType, null, null, null);
    }

    /** 拒绝（权限/风控拦截）的便捷方法 */
    public void recordDenied(String userId, String actionType, String conversationId, String detail) {
        record(userId, actionType, "denied", null, null, detail, conversationId, "system", null, null, null);
    }
}
