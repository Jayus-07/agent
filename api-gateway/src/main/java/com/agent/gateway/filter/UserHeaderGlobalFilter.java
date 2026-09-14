package com.agent.gateway.filter;

import com.agent.gateway.config.GatewayAuthProperties;
import com.agent.gateway.config.GatewayProperties;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * 用户身份注入过滤器（本地无认证演示模式）
 *
 * 将 X-User-Id 头注入下游请求（对接 Python 侧 TRUST_USER_HEADER 机制）。
 * 规则：
 *  - 请求已带 X-User-Id（App 客户端/渠道适配器注入）则透传
 *  - 未带且 user-header-enabled=true 时注入 default-user-id（本地开发/演示）
 *  - X-API-Key 由客户端自带，网关原样透传，鉴权仍在下游服务
 *
 * P2 变更：gateway.auth.enabled=true 时整体短路（02 文档 §4.3）。
 * 否则本过滤器注入的 demo-user 会覆盖真实身份；两者互斥，启动时已 fail-fast 校验。
 * 保留本过滤器 = 保留"本地完全无认证"的演示模式，语义并入 guest。
 */
@Component
public class UserHeaderGlobalFilter implements GlobalFilter, Ordered {

    private final GatewayProperties props;
    private final GatewayAuthProperties authProps;

    public UserHeaderGlobalFilter(GatewayProperties props, GatewayAuthProperties authProps) {
        this.props = props;
        this.authProps = authProps;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        if (authProps.isEnabled() || !props.isUserHeaderEnabled()) {
            return chain.filter(exchange);
        }
        var request = exchange.getRequest();
        if (request.getHeaders().containsKey(props.getUserHeaderName())) {
            return chain.filter(exchange);
        }
        var mutated = request.mutate()
                .header(props.getUserHeaderName(), props.getDefaultUserId())
                .build();
        return chain.filter(exchange.mutate().request(mutated).build());
    }

    @Override
    public int getOrder() {
        // 在路由转发前执行（晚于 AuthenticationGlobalFilter(-200)）
        return -10;
    }
}

