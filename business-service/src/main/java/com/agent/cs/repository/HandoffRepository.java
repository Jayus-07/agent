package com.agent.cs.repository;

import com.agent.cs.domain.Handoff;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface HandoffRepository extends JpaRepository<Handoff, Long> {

    /**
     * 对齐 HandoffRepository.load：该用户该会话未关闭的转接单
     */
    @Query("""
            SELECT h FROM Handoff h
            WHERE h.userId = :userId
              AND h.conversationId = :conversationId
              AND h.handoffState <> 'closed'
            """)
    Optional<Handoff> findActive(@Param("userId") String userId, @Param("conversationId") String conversationId);

    @Query("""
            SELECT h FROM Handoff h
            WHERE h.userId = :userId AND h.handoffState <> 'closed'
            ORDER BY h.createdAt DESC
            LIMIT 1
            """)
    Optional<Handoff> findLatestActive(@Param("userId") String userId);

    @Modifying
    @Query("UPDATE Handoff h SET h.handoffState = :state, h.closedAt = :closedAt, h.updatedAt = :updatedAt WHERE h.handoffId = :handoffId")
    int updateState(@Param("handoffId") String handoffId,
                    @Param("state") String state,
                    @Param("closedAt") OffsetDateTime closedAt,
                    @Param("updatedAt") OffsetDateTime updatedAt);

    boolean existsByUserIdAndHandoffStateNot(String userId, String handoffState);
}
