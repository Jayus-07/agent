package com.agent.gateway.filter;

import com.agent.gateway.auth.JwtClaims;
import com.agent.gateway.auth.JwtVerifier;
import com.agent.gateway.config.GatewayAuthProperties;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.util.AntPathMatcher;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/**
 * 网关认证执行过滤器（P2，order=-200，早于 {@link UserHeaderGlobalFilter}）。
 *
 * <p>唯一信任边界：本过滤器负责"令牌是真的、没过期、没被吊销"，随后注入可信身份头；
 * 下游一律信任网关注入的头（Python 侧 IDENTITY_SOURCE=header）。
 *
 * <p>执行顺序（02 文档 §4.1）：
 * <ol>
 *   <li>开关关闭 → 完全旁路（行为与改造前一致）</li>
 *   <li><b>无条件</b>剥离入站伪造身份头（旧 AuthFilter 缺此步，是本过滤器必须修正的缺陷之一）</li>
 *   <li>白名单（login/refresh/webhook）放行</li>
 *   <li>带 X-API-Key → 打标 {@code X-Auth-Type: api-key} 透传（双凭据共存，校验仍在下游）</li>
 *   <li>提取 Bearer → 黑名单（<b>Redis 异常 fail-closed</b>，旧实现 fail-open，缺陷之二）</li>
 *   <li>验签（签名/issuer/exp）→ 校验 {@code type=access}（拒绝拿 refresh 令牌当访问令牌用）</li>
 *   <li>按路由策略执行：OPEN/SHADOW 永不阻断（只记指标）；GUEST 无凭据注入游客；ENFORCE 一律 401</li>
 * </ol>
 */
@Component
public class AuthenticationGlobalFilter implements GlobalFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AuthenticationGlobalFilter.class);

    /** 网关注入的身份头协议（02 文档 §6）。 */
    public static final String HEADER_AUTH_TYPE = "X-Auth-Type";
    public static final String HEADER_USER_ID = "X-User-Id";
    public static final String HEADER_USER_NAME = "X-User-Name";
    public static final String HEADER_USER_DEPT = "X-User-Dept";
    public static final String HEADER_TRACE_ID = "X-Trace-Id";
    public static final String HEADER_API_KEY = "X-API-Key";

    public static final String AUTH_TYPE_JWT = "jwt";
    public static final String AUTH_TYPE_API_KEY = "api-key";
    public static final String AUTH_TYPE_ANONYMOUS = "anonymous";

    /** 游客身份（ARCH-10：游客 = user_id "anonymous" + subject_type customer 受限集，由 app 派生）。 */
    private static final String GUEST_USER_ID = "anonymous";

    private static final String METRIC_WOULD_DENY = "gateway_auth_would_deny_total";
    private static final String METRIC_DENIED = "gateway_auth_denied_total";

    private final GatewayAuthProperties props;
    private final JwtVerifier verifier;
    private final ReactiveStringRedisTemplate redis;
    private final MeterRegistry meterRegistry;
    private final AntPathMatcher pathMatcher = new AntPathMatcher();

    public AuthenticationGlobalFilter(GatewayAuthProperties props,
                                      JwtVerifier verifier,
                                      ReactiveStringRedisTemplate redis,
                                      MeterRegistry meterRegistry) {
        this.props = props;
        this.verifier = verifier;
        this.redis = redis;
        this.meterRegistry = meterRegistry;
    }

    @Override
    public int getOrder() {
        // 早于 UserHeaderGlobalFilter(-10)：认证先于任何身份注入
        return -200;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        if (!props.isEnabled()) {
            return chain.filter(exchange);
        }

        // ① 无条件剥离入站伪造头（白名单放行前，客户端在任何路径都无法自报身份）
        ServerWebExchange sanitized = stripForgedHeaders(exchange);
        ServerHttpRequest request = sanitized.getRequest();
        String path = request.getURI().getPath();

        // ② 非鉴权方法（CORS 预检）与白名单直接放行
        if (HttpMethod.OPTIONS.equals(request.getMethod()) || isExcluded(path)) {
            return chain.filter(sanitized);
        }

        GatewayAuthProperties.Policy policy = resolvePolicy(path);

        // ③ API-Key 通道：与 JWT 长期共存，网关只打标不校验（下游 api_key_middleware 负责）
        if (request.getHeaders().containsKey(HEADER_API_KEY)) {
            return chain.filter(withAuthType(sanitized, AUTH_TYPE_API_KEY));
        }

        String token = extractBearer(request);
        if (token == null) {
            return onNoCredential(sanitized, chain, policy, path);
        }

        // ④ 黑名单：登出/吊销后的令牌必须立刻失效
        return redis.hasKey(props.getJwt().getBlacklistPrefix() + token)
                .flatMap(hit -> {
                    if (Boolean.TRUE.equals(hit)) {
                        return denyOrShadow(sanitized, chain, policy, path, "blacklist", null);
                    }
                    return verifyAndContinue(sanitized, chain, policy, path, token);
                })
                // Redis 不可用 = 无法确认吊销状态 → fail-closed（旧实现 fail-open，缺陷之三）
                .onErrorResume(err -> denyOrShadow(sanitized, chain, policy, path,
                        "blacklist-unavailable", err));
    }

    private Mono<Void> verifyAndContinue(ServerWebExchange exchange, GatewayFilterChain chain,
                                         GatewayAuthProperties.Policy policy, String path, String token) {
        JwtClaims claims;
        try {
            claims = verifier.verify(token);
        } catch (JwtVerifier.TokenVerificationException e) {
            return denyOrShadow(exchange, chain, policy, path, e.getReason(), e);
        }
        // 刷新令牌不可当访问令牌用（否则 7 天有效的 refresh 等价于长效 access）
        if (!"access".equals(claims.type())) {
            return denyOrShadow(exchange, chain, policy, path, "token-type-mismatch", null);
        }
        if (isObserveOnly(policy)) {
            // 影子模式：指标已记为 would-deny=false，行为保持不注入
            return chain.filter(exchange);
        }
        ServerHttpRequest mutated = exchange.getRequest().mutate()
                .headers(headers -> {
                    headers.set(HEADER_AUTH_TYPE, AUTH_TYPE_JWT);
                    headers.set(HEADER_USER_ID, claims.userId());
                    if (claims.username() != null) {
                        headers.set(HEADER_USER_NAME, claims.username());
                    }
                    if (claims.dept() != null && !claims.dept().isBlank()) {
                        headers.set(HEADER_USER_DEPT, claims.dept());
                    }
                })
                .build();
        return chain.filter(exchange.mutate().request(mutated).build());
    }

    private Mono<Void> onNoCredential(ServerWebExchange exchange, GatewayFilterChain chain,
                                      GatewayAuthProperties.Policy policy, String path) {
        if (policy == GatewayAuthProperties.Policy.GUEST) {
            // 游客：注入显式游客身份（下游据此走受限只读集，不再依赖 "default" 兜底）
            ServerHttpRequest mutated = exchange.getRequest().mutate()
                    .headers(headers -> {
                        headers.set(HEADER_AUTH_TYPE, AUTH_TYPE_ANONYMOUS);
                        headers.set(HEADER_USER_ID, GUEST_USER_ID);
                    })
                    .build();
            return chain.filter(exchange.mutate().request(mutated).build());
        }
        return denyOrShadow(exchange, chain, policy, path, "no-credential", null);
    }

    /** 观测型策略：只记录"本该拒绝"，绝不改变行为。 */
    private static boolean isObserveOnly(GatewayAuthProperties.Policy policy) {
        return policy == GatewayAuthProperties.Policy.SHADOW || policy == GatewayAuthProperties.Policy.OPEN;
    }

    private Mono<Void> denyOrShadow(ServerWebExchange exchange, GatewayFilterChain chain,
                                    GatewayAuthProperties.Policy policy, String path,
                                    String reason, Throwable cause) {
        String route = resolveRouteTag(exchange);
        boolean wouldDenyOnly = isObserveOnly(policy);

        if (wouldDenyOnly) {
            meterRegistry.counter(METRIC_WOULD_DENY, "route", route, "reason", reason).increment();
            log.warn("[gateway-auth] shadow 模式本应拒绝：route={} path={} reason={}（已放行）{}",
                    route, path, reason, cause == null ? "" : "cause=" + cause.toString());
            return chain.filter(exchange);
        }

        meterRegistry.counter(METRIC_DENIED, "route", route, "reason", reason).increment();
        log.warn("[gateway-auth] 拒绝请求：route={} path={} reason={}", route, path, reason);
        return writeUnauthorized(exchange, reason);
    }

    /**
     * 剥离入站身份头。
     * 注意：必须用 {@code remove} 而非 {@code set}——旧 AuthFilter 用 {@code .header()} 是追加，
     * 下游取到的是客户端伪造值（缺陷之四）。
     */
    private ServerWebExchange stripForgedHeaders(ServerWebExchange exchange) {
        ServerHttpRequest sanitized = exchange.getRequest().mutate()
                .headers(headers -> props.getForgedHeaders().forEach(headers::remove))
                .build();
        return exchange.mutate().request(sanitized).build();
    }

    private ServerWebExchange withAuthType(ServerWebExchange exchange, String authType) {
        ServerHttpRequest mutated = exchange.getRequest().mutate()
                .headers(headers -> headers.set(HEADER_AUTH_TYPE, authType))
                .build();
        return exchange.mutate().request(mutated).build();
    }

    private boolean isExcluded(String path) {
        return props.getExcludePaths().stream().anyMatch(pattern -> pathMatcher.match(pattern, path));
    }

    private GatewayAuthProperties.Policy resolvePolicy(String path) {
        for (var entry : props.getRoutePolicies().entrySet()) {
            if (pathMatcher.match(entry.getKey(), path)) {
                return entry.getValue();
            }
        }
        return props.getDefaultPolicy();
    }

    private String resolveRouteTag(ServerWebExchange exchange) {
        try {
            Object route = exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR);
            if (route instanceof org.springframework.cloud.gateway.route.Route r) {
                return r.getId();
            }
        } catch (RuntimeException ignored) {
            // 路由未解析（如 404 前置）时退化为 unknown，不影响鉴权判定
        }
        return "unknown";
    }

    private String extractBearer(ServerHttpRequest request) {
        String header = request.getHeaders().getFirst(HttpHeaders.AUTHORIZATION);
        if (header == null || header.isBlank()) {
            return null;
        }
        String prefix = "Bearer ";
        if (!header.regionMatches(true, 0, prefix, 0, prefix.length())) {
            return null;
        }
        String token = header.substring(prefix.length()).trim();
        return token.isEmpty() ? null : token;
    }

    /** 统一 401 响应体（与旧系统 Result 结构一致：code/message/data/timestamp）。 */
    private Mono<Void> writeUnauthorized(ServerWebExchange exchange, String reason) {
        var response = exchange.getResponse();
        response.setStatusCode(HttpStatus.UNAUTHORIZED);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        response.getHeaders().set(HEADER_TRACE_ID, traceId(exchange));
        String body = "{\"code\":401,\"message\":\"未认证：" + reason
                + "\",\"data\":null,\"timestamp\":" + System.currentTimeMillis() + "}";
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }

    private String traceId(ServerWebExchange exchange) {
        String existing = exchange.getRequest().getHeaders().getFirst(HEADER_TRACE_ID);
        return (existing == null || existing.isBlank()) ? UUID.randomUUID().toString() : existing;
    }
}
