package com.agent.cs.web;

import com.agent.cs.config.InternalTokenFilter;
import com.agent.cs.service.ConversationService;
import com.agent.cs.service.StateTransitionService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 内部 REST — 供 Python AI 服务调用的过渡接口（cutover 阶段）
 *
 * Python 侧切换 CS_ADMIN_SOURCE=java / 状态写入走 Java 时使用。
 * 由 InternalTokenFilter 保护（X-Internal-Token）。
 */
@RestController
@RequestMapping("/internal")
public class InternalConversationController {

    private final StateTransitionService stateTransitionService;
    private final ConversationService conversationService;

    public InternalConversationController(StateTransitionService stateTransitionService,
                                          ConversationService conversationService) {
        this.stateTransitionService = stateTransitionService;
        this.conversationService = conversationService;
    }

    /**
     * 对齐 Python StateTransitionService.apply()：
     * POST /internal/state-transitions
     */
    @PostMapping("/state-transitions")
    public StateTransitionService.StateTransitionResult applyTransition(
            @RequestBody StateTransitionService.StateTransitionRequest request) {
        return stateTransitionService.apply(request);
    }

    /**
     * 对齐 Python StateTransitionService.load_snapshot()：
     * GET /internal/state-snapshot?user_id=..&session_id=..&conversation_id=..
     */
    @GetMapping("/state-snapshot")
    public Map<String, Object> loadSnapshot(@RequestParam String user_id,
                                            @RequestParam String session_id,
                                            @RequestParam(required = false) String conversation_id) {
        return stateTransitionService.loadSnapshot(user_id, session_id, conversation_id);
    }

    /**
     * 消息落库（cutover 后 Python 的 conversation_store.save_turn 改走这里）：
     * POST /internal/messages
     * body: { conversation_id, user_id, sender_type, content, content_type?, trace_id?, private?, metadata? }
     */
    @PostMapping("/messages")
    public Map<String, Object> saveMessage(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        String conversationId = String.valueOf(body.get("conversation_id"));
        String userId = String.valueOf(body.get("user_id"));
        var saved = conversationService.saveMessage(
                conversationId,
                userId,
                str(body.get("sender_type"), "user"),
                String.valueOf(body.getOrDefault("content", "")),
                str(body.get("content_type"), "text"),
                str(body.get("trace_id"), null),
                Boolean.TRUE.equals(body.get("private")),
                (Map<String, Object>) body.get("metadata"));
        return Map.of(
                "message_id", saved.getMessageId(),
                "created_at", saved.getCreatedAt().toString());
    }

    @SuppressWarnings("unchecked")
    private static String str(Object v, String def) {
        return v == null ? def : String.valueOf(v);
    }
}
