package com.agent.cs.whatsapp;

import com.agent.cs.config.AppProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * WhatsApp Cloud API 发送器（Graph API v20.0）
 *
 * 无 WHATSAPP_ACCESS_TOKEN 时优雅降级为仅收不发（canSend()=false）。
 */
@Component
public class WhatsAppSender {

    private static final Logger log = LoggerFactory.getLogger(WhatsAppSender.class);

    private final AppProperties props;
    private final RestClient restClient;

    public WhatsAppSender(AppProperties props) {
        this.props = props;
        this.restClient = RestClient.create();
    }

    /**
     * 发送文本消息。
     *
     * @param toPhone 接收方手机号（E.164，不带 +）
     * @param text    消息文本
     * @return 是否发送成功（降级模式返回 false）
     */
    public boolean sendText(String toPhone, String text) {
        if (!props.getWhatsapp().canSend()) {
            log.warn("[WhatsApp] access token not configured, skip sending to {}", toPhone);
            return false;
        }
        String url = "%s/%s/messages".formatted(
                props.getWhatsapp().getGraphApiBase(),
                props.getWhatsapp().getPhoneNumberId());
        try {
            Map<String, Object> body = Map.of(
                    "messaging_product", "whatsapp",
                    "recipient_type", "individual",
                    "to", toPhone,
                    "type", "text",
                    "text", Map.of("preview_url", false, "body", text));
            String resp = restClient.post()
                    .uri(url)
                    .header("Authorization", "Bearer " + props.getWhatsapp().getAccessToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(body)
                    .retrieve()
                    .body(String.class);
            log.info("[WhatsApp] sent to {}: {}", toPhone, resp == null ? "ok" : resp.substring(0, Math.min(200, resp.length())));
            return true;
        } catch (Exception e) {
            log.error("[WhatsApp] send to {} failed: {}", toPhone, e.getMessage());
            return false;
        }
    }
}
