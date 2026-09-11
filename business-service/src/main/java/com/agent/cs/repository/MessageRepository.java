package com.agent.cs.repository;

import com.agent.cs.domain.Message;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface MessageRepository extends JpaRepository<Message, Long> {

    long countByConversationId(String conversationId);

    /**
     * 会话详情用：过滤 private 内部备注，按时间正序（对齐 cs_admin.py）
     */
    @Query("""
            SELECT m FROM Message m
            WHERE m.conversation.conversationId = :conversationId
              AND m.isPrivate = false
            ORDER BY m.createdAt ASC
            """)
    List<Message> findVisibleByConversationId(@Param("conversationId") String conversationId);

    @Query("""
            SELECT DISTINCT m.traceId FROM Message m
            WHERE m.conversation.conversationId = :conversationId
              AND m.traceId IS NOT NULL
            ORDER BY m.traceId
            """)
    List<String> findDistinctTraceIds(@Param("conversationId") String conversationId);
}
