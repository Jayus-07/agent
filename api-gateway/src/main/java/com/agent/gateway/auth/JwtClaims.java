package com.agent.gateway.auth;

/**
 * 校验通过后的身份事实（网关侧只读投影）。
 *
 * <p>对应 auth-service 签发令牌中的 claims：userId/username/type/deviceId(+dept)。
 * 网关据此注入 X-User-* 头，下游（app/business-service）不再解析令牌。
 */
public record JwtClaims(
        String userId,
        String username,
        String dept,
        String type,
        String deviceId
) {
}
