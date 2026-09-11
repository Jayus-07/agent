package com.agent.cs.repository;

import com.agent.cs.domain.Confirmation;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.Optional;

public interface ConfirmationRepository extends JpaRepository<Confirmation, Long> {

    /**
     * 对齐 ConfirmationRepository.load：取该用户该会话的 pending 确认单
     */
    Optional<Confirmation> findByUserIdAndConversationIdAndState(String userId, String conversationId, String state);

    default Optional<Confirmation> findPending(String userId, String conversationId) {
        return findByUserIdAndConversationIdAndState(userId, conversationId, "pending");
    }

    @Modifying
    @Query("UPDATE Confirmation c SET c.state = :state, c.confirmedAt = :confirmedAt, c.executedAt = :executedAt WHERE c.confirmationId = :confirmationId")
    int updateState(@Param("confirmationId") String confirmationId,
                    @Param("state") String state,
                    @Param("confirmedAt") OffsetDateTime confirmedAt,
                    @Param("executedAt") OffsetDateTime executedAt);

    @Modifying
    @Query("UPDATE Confirmation c SET c.state = 'cancelled' WHERE c.userId = :userId AND c.conversationId = :conversationId AND c.state = 'pending'")
    int clearPending(@Param("userId") String userId, @Param("conversationId") String conversationId);

    boolean existsByUserIdAndState(String userId, String state);
}
