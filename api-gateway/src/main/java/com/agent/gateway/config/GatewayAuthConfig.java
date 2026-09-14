package com.agent.gateway.config;

import org.springframework.context.annotation.Configuration;

/**
 * 网关鉴权的启动校验（P2）。
 *
 * <p>黑名单用的 Redis 连接统一走 Spring 标准的 {@code spring.data.redis.*}
 * （由 application.yml 映射到 oa-auth-redis，非 Spring Boot 默认 localhost:6379），
 * 因此这里不再自建连接工厂——自建会与 RedisReactiveAutoConfiguration 的
 * reactiveStringRedisTemplate 形成两个同类型 Bean，令 Spring Cloud Gateway 的
 * GatewayRedisAutoConfiguration（redisRateLimiter）无法选定依赖而启动失败（P2 实测踩坑）。
 *
 * <p>本类只做一件事：配置错误时启动即失败。
 * 否则表现是"全部请求 401"，排查成本远高于启动报错。
 */
@Configuration
public class GatewayAuthConfig {

    private final GatewayAuthProperties authProps;
    private final GatewayProperties gatewayProps;

    public GatewayAuthConfig(GatewayAuthProperties authProps, GatewayProperties gatewayProps) {
        this.authProps = authProps;
        this.gatewayProps = gatewayProps;
        validate();
    }

    /** 启动 fail-fast（02 文档 §4.2）。 */
    private void validate() {
        if (!authProps.isEnabled()) {
            return;
        }
        String secret = authProps.getJwt().getSecret();
        if (secret == null || secret.isBlank()) {
            throw new IllegalStateException(
                    "gateway.auth.enabled=true 但 gateway.auth.jwt.secret 为空（JWT_SECRET 未配置），拒绝启动");
        }
        if (secret.getBytes(java.nio.charset.StandardCharsets.UTF_8).length < 32) {
            throw new IllegalStateException(
                    "gateway.auth.jwt.secret 长度不足 32 字节，与 auth-service 签发侧不一致，拒绝启动");
        }
        if (authProps.getJwt().getIssuer() == null || authProps.getJwt().getIssuer().isBlank()) {
            throw new IllegalStateException("gateway.auth.jwt.issuer 为空，拒绝启动");
        }
        // 互斥：身份头注入器会覆盖真实身份，两者不得同时开启
        if (gatewayProps.isUserHeaderEnabled()) {
            throw new IllegalStateException(
                    "gateway.auth.enabled=true 与 gateway.user-header-enabled=true 互斥，拒绝启动");
        }
    }
}
