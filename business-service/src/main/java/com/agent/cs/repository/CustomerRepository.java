package com.agent.cs.repository;

import com.agent.cs.domain.Customer;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface CustomerRepository extends JpaRepository<Customer, Long> {

    Optional<Customer> findByCustomerId(String customerId);

    Optional<Customer> findByAuthProviderAndAuthExternalId(String authProvider, String authExternalId);
}
