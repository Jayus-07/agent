package com.agent.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 网关鉴权配置（P2）。
 *
 * <p>对应 application.yml 的 {@code gateway.auth.*}；与 {@link GatewayProperties}
 * （{@code gateway.user-header-enabled} 等）是独立绑定类，互不影响。
 *
 * <p>设计依据：docs/auth/02-详细架构设计.md §4.1/§4.2。
 * 黑名单用的 Redis 连接见 {@code spring.data.redis.*}（application.yml 映射到 oa-auth-redis）。
 */
@ConfigurationProperties(prefix = "gateway.auth")
public class GatewayAuthProperties {

    /** 鉴权总开关。false = 完全旁路，行为与改造前一致（默认值，符合灰度要求）。 */
    private boolean enabled = false;

    /** 未在 route-policies 命中的路由使用的默认策略。 */
    private Policy defaultPolicy = Policy.SHADOW;

    /** 白名单：不做鉴权直接放行（仍会剥离伪造头）。 */
    private List<String> excludePaths = new ArrayList<>(List.of(
            "/api/auth/login",
            "/api/auth/refresh",
            "/api/channels/whatsapp/webhook"
    ));

    /** 路由级策略：Ant 风格路径 → 策略，按声明顺序取首个命中。 */
    private Map<String, Policy> routePolicies = new LinkedHashMap<>();

    /** 不鉴权模式下要剥离的入站身份头（防伪造，无条件执行）。 */
    private List<String> forgedHeaders = new ArrayList<>(List.of(
            "X-User-Id", "X-User-Name", "X-User-Dept", "X-Auth-Type"
    ));

    private final Jwt jwt = new Jwt();

    /** 鉴权策略（灰度阶梯，见 02 文档 §10）。 */
    public enum Policy {
        /** 现状：不鉴权、不注入，直接放行。 */
        OPEN,
        /** 影子：只记指标/日志，永不阻断（用于上线前观测）。 */
        SHADOW,
        /** 游客：无凭据时注入游客身份放行；有凭据但无效仍 401。 */
        GUEST,
        /** 强制：无凭据或凭据无效一律 401。 */
        ENFORCE
    }

    public static class Jwt {
        /** HS 共享密钥，与 auth-service 同值（≥32 字节，启动校验）。 */
        private String secret = "";
        /**
         * 签发者，必须与 auth-service 的 jwt.issuer 一致。
         * 实测现状为 hongmeng-oa（P2-D1 双端对齐）；错配会导致全部令牌 401。
         */
        private String issuer = "hongmeng-oa";
        /** 黑名单前缀，与 auth-service PermissionCache 写侧一致（旧值 token:blacklist: 已作废）。 */
        private String blacklistPrefix = "auth:blacklist:";
        /** 时钟偏移容忍（秒）。 */
        private long clockSkewSeconds = 60;
        /** 迁移期双密钥：轮换时旧密钥暂留验签窗口（可选）。 */
        private String previousSecret = "";

        public String getSecret() { return secret; }
        public void setSecret(String secret) { this.secret = secret; }

        public String getIssuer() { return issuer; }
        public void setIssuer(String issuer) { this.issuer = issuer; }

        public String getBlacklistPrefix() { return blacklistPrefix; }
        public void setBlacklistPrefix(String blacklistPrefix) { this.blacklistPrefix = blacklistPrefix; }

        public long getClockSkewSeconds() { return clockSkewSeconds; }
        public void setClockSkewSeconds(long clockSkewSeconds) { this.clockSkewSeconds = clockSkewSeconds; }

        public String getPreviousSecret() { return previousSecret; }
        public void setPreviousSecret(String previousSecret) { this.previousSecret = previousSecret; }
    }

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }

    public Policy getDefaultPolicy() { return defaultPolicy; }
    public void setDefaultPolicy(Policy defaultPolicy) { this.defaultPolicy = defaultPolicy; }

    public List<String> getExcludePaths() { return excludePaths; }
    public void setExcludePaths(List<String> excludePaths) { this.excludePaths = excludePaths; }

    public Map<String, Policy> getRoutePolicies() { return routePolicies; }
    public void setRoutePolicies(Map<String, Policy> routePolicies) { this.routePolicies = routePolicies; }

    public List<String> getForgedHeaders() { return forgedHeaders; }
    public void setForgedHeaders(List<String> forgedHeaders) { this.forgedHeaders = forgedHeaders; }

    public Jwt getJwt() { return jwt; }
}
