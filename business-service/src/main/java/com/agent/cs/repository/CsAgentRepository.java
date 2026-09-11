package com.agent.cs.repository;

import com.agent.cs.domain.CsAgent;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface CsAgentRepository extends JpaRepository<CsAgent, Long> {

    Optional<CsAgent> findByAgentId(String agentId);
}
