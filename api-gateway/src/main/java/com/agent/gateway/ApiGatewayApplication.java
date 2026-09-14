package com.agent.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * API Gateway 入口（Spring Cloud Gateway，WebFlux 响应式栈）
 *
 * <p>鉴权黑名单用的 Redis 连接由 {@code gateway.auth.redis.*} 显式指定
 * （oa-auth-redis 独立实例，见 GatewayAuthConfig），不依赖 spring.data.redis.*；
 * 但 Spring Boot 的 Redis 自动配置保留不动——Spring Cloud Gateway 的
 * GatewayRedisAutoConfiguration 需要名为 redisTemplate 的 Bean，排除后会启动失败。
 * 本地无 Redis 时不影响健康：management.health.redis.enabled=false。
 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class ApiGatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(ApiGatewayApplication.class, args);
    }
}
