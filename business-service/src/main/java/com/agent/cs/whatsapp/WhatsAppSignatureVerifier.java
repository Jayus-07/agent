package com.agent.cs.whatsapp;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * WhatsApp Cloud API webhook 签名校验
 * X-Hub-Signature-256: sha256=HMAC-SHA256(app_secret, raw_body)
 */
public class WhatsAppSignatureVerifier {

    private WhatsAppSignatureVerifier() {
    }

    public static boolean verify(String appSecret, String signatureHeader, String rawBody) {
        if (appSecret == null || appSecret.isBlank()) {
            // 未配置 app_secret 时跳过校验（本地开发）
            return true;
        }
        if (signatureHeader == null || !signatureHeader.startsWith("sha256=")) {
            return false;
        }
        String expected = "sha256=" + hmacSha256Hex(appSecret, rawBody);
        return MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                signatureHeader.getBytes(StandardCharsets.UTF_8));
    }

    private static String hmacSha256Hex(String secret, String payload) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] digest = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(digest.length * 2);
            for (byte b : digest) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (Exception e) {
            throw new IllegalStateException("HMAC-SHA256 computation failed", e);
        }
    }
}
