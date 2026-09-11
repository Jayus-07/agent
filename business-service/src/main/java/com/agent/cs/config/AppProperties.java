package com.agent.cs.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

/**
 * 应用配置（对齐 backend/config/customer_service.py 的风控阈值与渠道配置）
 */
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private final BizDb bizDb = new BizDb();
    private final Risk risk = new Risk();
    private final Whatsapp whatsapp = new Whatsapp();
    private final AiService aiService = new AiService();
    private String internalApiToken = "";

    public BizDb getBizDb() { return bizDb; }
    public Risk getRisk() { return risk; }
    public Whatsapp getWhatsapp() { return whatsapp; }
    public AiService getAiService() { return aiService; }
    public String getInternalApiToken() { return internalApiToken; }
    public void setInternalApiToken(String internalApiToken) { this.internalApiToken = internalApiToken; }

    public static class BizDb {
        private String url;
        private String username;
        private String password;

        public String getUrl() { return url; }
        public void setUrl(String url) { this.url = url; }
        public String getUsername() { return username; }
        public void setUsername(String username) { this.username = username; }
        public String getPassword() { return password; }
        public void setPassword(String password) { this.password = password; }
    }

    public static class Risk {
        /** 高风险动作（至少 HIGH）：退款/退货 */
        private List<String> highRiskActions = List.of("refund_request", "refund_execute", "return_request", "return_execute");
        /** 临界动作（直接 CRITICAL）：批量退款、删号 */
        private List<String> criticalActions = List.of("batch_refund", "account_delete");
        /** 金额达到该值升级 CRITICAL */
        private double criticalRefundAmount = 5000;

        public List<String> getHighRiskActions() { return highRiskActions; }
        public void setHighRiskActions(List<String> highRiskActions) { this.highRiskActions = highRiskActions; }
        public List<String> getCriticalActions() { return criticalActions; }
        public void setCriticalActions(List<String> criticalActions) { this.criticalActions = criticalActions; }
        public double getCriticalRefundAmount() { return criticalRefundAmount; }
        public void setCriticalRefundAmount(double criticalRefundAmount) { this.criticalRefundAmount = criticalRefundAmount; }
    }

    /** AI 服务（Python ai-service）连接配置，供 AiClient 调用 /internal/ai/* 工具网关 */
    public static class AiService {
        private String baseUrl = "http://localhost:8000";
        /** 调用 ai-service /internal/ai/* 的令牌；为空则不携带（本地开发模式） */
        private String internalToken = "";
        private long timeoutMs = 60000;

        public String getBaseUrl() { return baseUrl; }
        public void setBaseUrl(String baseUrl) { this.baseUrl = baseUrl; }
        public String getInternalToken() { return internalToken; }
        public void setInternalToken(String internalToken) { this.internalToken = internalToken; }
        public long getTimeoutMs() { return timeoutMs; }
        public void setTimeoutMs(long timeoutMs) { this.timeoutMs = timeoutMs; }
    }

    public static class Whatsapp {
        private String accessToken = "";
        private String phoneNumberId = "";
        private String verifyToken = "";
        private String appSecret = "";
        private String graphApiBase = "https://graph.facebook.com/v20.0";

        public String getAccessToken() { return accessToken; }
        public void setAccessToken(String accessToken) { this.accessToken = accessToken; }
        public String getPhoneNumberId() { return phoneNumberId; }
        public void setPhoneNumberId(String phoneNumberId) { this.phoneNumberId = phoneNumberId; }
        public String getVerifyToken() { return verifyToken; }
        public void setVerifyToken(String verifyToken) { this.verifyToken = verifyToken; }
        public String getAppSecret() { return appSecret; }
        public void setAppSecret(String appSecret) { this.appSecret = appSecret; }
        public String getGraphApiBase() { return graphApiBase; }
        public void setGraphApiBase(String graphApiBase) { this.graphApiBase = graphApiBase; }

        /** 无 token 时优雅降级为仅收不发 */
        public boolean canSend() {
            return accessToken != null && !accessToken.isBlank()
                    && phoneNumberId != null && !phoneNumberId.isBlank();
        }
    }
}
