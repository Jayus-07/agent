package com.agent.gateway.auth;

/**
 * 令牌验签抽象（ARCH-01 预留演进点）。
 *
 * <p>本期唯一实现 {@link HmacJwtVerifier}（HS 共享密钥，仅 auth-service 签发 + 网关验签两处持有）；
 * 阶段二触发条件（Python 需独立验签 / 第三方接入）时新增 JwksJwtVerifier，claims 与下游头协议不变。
 */
public interface JwtVerifier {

    /**
     * 验签并解析令牌。
     *
     * @param token 原始 JWT（不含 "Bearer " 前缀）
     * @return 身份事实
     * @throws TokenVerificationException 签名不符 / 签发者不符 / 已过期 / 结构非法
     */
    JwtClaims verify(String token);

    /** 验签失败（区分签名、issuer、过期、格式四类原因，便于日志与指标打标）。 */
    class TokenVerificationException extends RuntimeException {
        private final String reason;

        public TokenVerificationException(String reason, String message) {
            super(message);
            this.reason = reason;
        }

        public TokenVerificationException(String reason, String message, Throwable cause) {
            super(message, cause);
            this.reason = reason;
        }

        public String getReason() {
            return reason;
        }
    }
}
