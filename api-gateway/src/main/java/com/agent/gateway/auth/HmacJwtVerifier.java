package com.agent.gateway.auth;

import com.agent.gateway.config.GatewayAuthProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.IncorrectClaimException;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.MalformedJwtException;
import io.jsonwebtoken.UnsupportedJwtException;
import io.jsonwebtoken.security.Keys;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;

/**
 * HS 共享密钥验签实现（ARCH-01 阶段一）。
 *
 * <p>密钥同时由 auth-service 签发侧与网关验签侧持有，算法由 JJWT 按密钥长度自动选择
 * （实测 64 字节密钥 → HS512）。校验项：签名 + issuer + exp（含时钟偏移容忍）。
 *
 * <p>注意：本类只负责"令牌是真的、没过期、是本系统签的"；
 * "是不是 access 类型、有没有被吊销"由过滤器判断。
 */
@Component
public class HmacJwtVerifier implements JwtVerifier {

    private final GatewayAuthProperties.Jwt props;
    private volatile SecretKey signingKey;
    private volatile SecretKey previousKey;
    private volatile boolean keysInitialized;

    public HmacJwtVerifier(GatewayAuthProperties authProps) {
        this.props = authProps.getJwt();
        // 注意：不在构造期校验密钥——鉴权开关默认关闭（enabled=false）时，
        // 本 Bean 仍会被实例化，密钥为空属正常状态；密钥校验由 GatewayAuthConfig
        // 在 enabled=true 时 fail-fast，这里只在首次验签时惰性构建。
    }

    private static SecretKey toKey(String secret, String propName) {
        if (secret == null || secret.isBlank()) {
            throw new JwtVerifier.TokenVerificationException("keystore-unavailable", propName + " 未配置");
        }
        byte[] bytes = secret.getBytes(StandardCharsets.UTF_8);
        if (bytes.length < 32) {
            throw new JwtVerifier.TokenVerificationException("keystore-unavailable",
                    propName + " 长度不足（" + bytes.length + " 字节，至少 32 字节）");
        }
        return Keys.hmacShaKeyFor(bytes);
    }

    private void ensureKeys() {
        if (keysInitialized) {
            return;
        }
        synchronized (this) {
            if (keysInitialized) {
                return;
            }
            this.signingKey = toKey(props.getSecret(), "gateway.auth.jwt.secret");
            String previous = props.getPreviousSecret();
            this.previousKey = (previous == null || previous.isBlank())
                    ? null
                    : toKey(previous, "gateway.auth.jwt.secret-previous");
            this.keysInitialized = true;
        }
    }

    @Override
    public JwtClaims verify(String token) {
        ensureKeys();
        try {
            return extract(parse(token, signingKey));
        } catch (JwtVerifier.TokenVerificationException e) {
            // 双密钥轮换窗口：主密钥失败时用旧密钥再试一次（仅验签，不做其他放宽）
            if (previousKey != null && "signature".equals(e.getReason())) {
                return extract(parse(token, previousKey));
            }
            throw e;
        }
    }

    private Claims parse(String token, SecretKey key) {
        try {
            return Jwts.parser()
                    .verifyWith(key)
                    .requireIssuer(props.getIssuer())
                    .clockSkewSeconds(props.getClockSkewSeconds())
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
        } catch (ExpiredJwtException e) {
            throw new JwtVerifier.TokenVerificationException("expired", "令牌已过期", e);
        } catch (IncorrectClaimException e) {
            throw new JwtVerifier.TokenVerificationException("issuer",
                    "签发者不匹配（期望 " + props.getIssuer() + "）", e);
        } catch (io.jsonwebtoken.security.SignatureException e) {
            throw new JwtVerifier.TokenVerificationException("signature", "签名校验失败", e);
        } catch (MalformedJwtException e) {
            throw new JwtVerifier.TokenVerificationException("malformed", "令牌格式非法", e);
        } catch (UnsupportedJwtException e) {
            throw new JwtVerifier.TokenVerificationException("unsupported", "不支持的令牌类型", e);
        } catch (JwtException | IllegalArgumentException e) {
            throw new JwtVerifier.TokenVerificationException("invalid", "令牌校验失败", e);
        }
    }

    private JwtClaims extract(Claims claims) {
        Object rawUserId = claims.get("userId");
        String userId = rawUserId == null ? null : String.valueOf(rawUserId);
        if (userId == null || userId.isBlank()) {
            throw new JwtVerifier.TokenVerificationException("missing-user-id", "令牌缺少 userId claim");
        }
        return new JwtClaims(
                userId,
                stringOrNull(claims.get("username")),
                stringOrNull(claims.get("dept")),      // ARCH-13：dept_code，未接入前为空
                stringOrNull(claims.get("type")),
                stringOrNull(claims.get("deviceId"))
        );
    }

    private static String stringOrNull(Object value) {
        return value == null ? null : String.valueOf(value);
    }
}
