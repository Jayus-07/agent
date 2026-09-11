package com.agent.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 网关身份注入配置
 */
@ConfigurationProperties(prefix = "gateway")
public class GatewayProperties {

    /** 是否开启 X-User-Id 注入（需与 Python 侧 TRUST_USER_HEADER 配套） */
    private boolean userHeaderEnabled = false;
    private String userHeaderName = "X-User-Id";
    private String defaultUserId = "demo-user";

    public boolean isUserHeaderEnabled() { return userHeaderEnabled; }
    public void setUserHeaderEnabled(boolean userHeaderEnabled) { this.userHeaderEnabled = userHeaderEnabled; }
    public String getUserHeaderName() { return userHeaderName; }
    public void setUserHeaderName(String userHeaderName) { this.userHeaderName = userHeaderName; }
    public String getDefaultUserId() { return defaultUserId; }
    public void setDefaultUserId(String defaultUserId) { this.defaultUserId = defaultUserId; }
}
