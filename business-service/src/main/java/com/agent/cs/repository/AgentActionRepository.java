package com.agent.cs.repository;

import com.agent.cs.domain.AgentAction;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface AgentActionRepository extends JpaRepository<AgentAction, Long> {

    Optional<AgentAction> findByActionId(String actionId);
}
