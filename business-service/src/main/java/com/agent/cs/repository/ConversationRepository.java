package com.agent.cs.repository;

import com.agent.cs.domain.Conversation;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface ConversationRepository extends JpaRepository<Conversation, Long> {

    Optional<Conversation> findByConversationId(String conversationId);

    /**
     * keyset 分页（对齐 cs_admin.py：按 last_activity_at DESC，cursor 为上一页最后一条的时间）
     */
    @Query("""
            SELECT c FROM Conversation c
            WHERE (:status IS NULL OR c.conversationStatus = :status)
              AND (:handlingMode IS NULL OR c.handlingMode = :handlingMode)
              AND (:userId IS NULL OR c.userId = :userId)
              AND (:cursor IS NULL OR c.lastActivityAt < :cursor)
              AND (:q IS NULL OR LOWER(c.summary) LIKE LOWER(CONCAT('%', :q, '%')))
            ORDER BY c.lastActivityAt DESC
            """)
    List<Conversation> listPage(@Param("status") String status,
                                @Param("handlingMode") String handlingMode,
                                @Param("userId") String userId,
                                @Param("cursor") OffsetDateTime cursor,
                                @Param("q") String q,
                                Pageable pageable);

    @Query("""
            SELECT COUNT(c) FROM Conversation c
            WHERE (:status IS NULL OR c.conversationStatus = :status)
              AND (:handlingMode IS NULL OR c.handlingMode = :handlingMode)
              AND (:userId IS NULL OR c.userId = :userId)
              AND (:q IS NULL OR LOWER(c.summary) LIKE LOWER(CONCAT('%', :q, '%')))
            """)
    long countFiltered(@Param("status") String status,
                       @Param("handlingMode") String handlingMode,
                       @Param("userId") String userId,
                       @Param("q") String q);
}
