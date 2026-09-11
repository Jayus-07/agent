package com.agent.cs.config;

import com.zaxxer.hikari.HikariDataSource;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.autoconfigure.jdbc.DataSourceProperties;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.orm.jpa.EntityManagerFactoryBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.orm.jpa.JpaTransactionManager;
import org.springframework.orm.jpa.LocalContainerEntityManagerFactoryBean;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.EnableTransactionManagement;

import javax.sql.DataSource;
import java.util.Objects;

/**
 * 双数据源：
 *  - memory DB（agent_memory，读写）：customer_service schema 的全部实体 → JPA
 *  - business DB（agent_business，只读账号）：订单等业务数据 → 仅 JdbcTemplate 查询
 *
 * 与 Python 侧 backend/memory/database.py + backend/sql/executor.py 的双库布局一致。
 */
@Configuration
@EnableTransactionManagement
public class DataSourcesConfig {

    // ── 主数据源：记忆库（spring.datasource.* 自动配置属性） ──

    @Bean
    @Primary
    @ConfigurationProperties("spring.datasource")
    public DataSourceProperties memoryDataSourceProperties() {
        return new DataSourceProperties();
    }

    @Bean
    @Primary
    public DataSource memoryDataSource() {
        return memoryDataSourceProperties()
                .initializeDataSourceBuilder()
                .type(HikariDataSource.class)
                .build();
    }

    @Bean
    @Primary
    public LocalContainerEntityManagerFactoryBean entityManagerFactory(
            EntityManagerFactoryBuilder builder,
            @Qualifier("memoryDataSource") DataSource memoryDataSource) {
        return builder
                .dataSource(memoryDataSource)
                .packages("com.agent.cs.domain")
                .build();
    }

    @Bean
    @Primary
    public PlatformTransactionManager transactionManager(
            @Qualifier("entityManagerFactory") LocalContainerEntityManagerFactoryBean emf) {
        return new JpaTransactionManager(Objects.requireNonNull(emf.getObject()));
    }

    @EnableJpaRepositories(
            basePackages = "com.agent.cs.repository",
            entityManagerFactoryRef = "entityManagerFactory",
            transactionManagerRef = "transactionManager"
    )
    static class JpaRepositoriesConfig {
    }

    // ── 第二数据源：业务库（只读） ──

    @Bean
    @ConfigurationProperties("app.biz-db")
    public DataSourceProperties bizDataSourceProperties() {
        return new DataSourceProperties();
    }

    @Bean(name = "bizDataSource")
    public DataSource bizDataSource() {
        return bizDataSourceProperties()
                .initializeDataSourceBuilder()
                .type(HikariDataSource.class)
                .build();
    }

    @Bean(name = "bizJdbcTemplate")
    public JdbcTemplate bizJdbcTemplate(@Qualifier("bizDataSource") DataSource bizDataSource) {
        return new JdbcTemplate(bizDataSource);
    }
}
