package com.agent.cs.web;

import com.agent.cs.service.ConversationService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * CS Admin API — 会话列表 / 详情（对齐 cs_admin.py，前端 cs.ts 无需改动）
 *
 * 路由经 API Gateway：/api/cs/conversations → 本服务 /cs/conversations
 */
@RestController
@RequestMapping("/cs/conversations")
public class CsConversationController {

    private final ConversationService conversationService;

    public CsConversationController(ConversationService conversationService) {
        this.conversationService = conversationService;
    }

    @GetMapping
    public Map<String, Object> listConversations(
            @RequestParam(defaultValue = "20") int limit,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String handling_mode,
            @RequestParam(required = false) String user_id,
            @RequestParam(required = false) String q) {
        return conversationService.listConversations(limit, cursor, status, handling_mode, user_id, q);
    }

    @GetMapping("/{conversationId}")
    public Map<String, Object> getConversation(@PathVariable String conversationId) {
        return conversationService.getConversationDetail(conversationId);
    }

    /**
     * trace 摘要：返回 trace_id 列表。
     * 完整 trace 数据仍在 Python 侧（observability.trace_store），前端如需详情
     * 走网关 /api/observability/... 查询。
     */
    @GetMapping("/{conversationId}/traces")
    public Map<String, Object> getConversationTraces(@PathVariable String conversationId) {
        List<String> traceIds = conversationService.getTraceIds(conversationId);
        return Map.of("conversation_id", conversationId, "trace_ids", traceIds);
    }
}
