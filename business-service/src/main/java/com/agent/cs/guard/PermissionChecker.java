package com.agent.cs.guard;

import com.agent.cs.common.ApiException;

import java.util.regex.Pattern;

/**
 * 权限校验层（直译自 backend/customer_service/security/permission.py）
 *
 * 身份验证 + 资源归属校验，确保用户只能访问自己的数据。
 */
public class PermissionChecker {

    private static final Pattern ORDER_ID_RE = Pattern.compile("^[a-zA-Z0-9\\-]+$");

    private PermissionChecker() {
    }

    /**
     * 从 cs_context 提取 authenticated_user_id。
     *
     * @throws ApiException 身份缺失或 anonymous
     */
    public static String validateUserIdentity(String authenticatedUserId) {
        if (authenticatedUserId == null || authenticatedUserId.isBlank() || "anonymous".equals(authenticatedUserId)) {
            throw ApiException.authentication("user_id missing or anonymous in cs_context");
        }
        return authenticatedUserId;
    }

    /**
     * 校验 order_id 格式。
     *
     * @throws ApiException 格式不合法
     */
    public static String validateOrderId(String orderId) {
        if (orderId == null || orderId.isBlank()) {
            throw ApiException.validation("order_id 不能为空");
        }
        if (!ORDER_ID_RE.matcher(orderId).matches() || orderId.length() > 64) {
            throw ApiException.validation("order_id 格式不合法: " + orderId);
        }
        return orderId;
    }

    /**
     * 验证订单归属权。
     *
     * @param userId        当前认证用户
     * @param orderCustomerId 订单数据中的 customer_id
     * @throws ApiException 订单不属于该用户
     */
    public static void checkOrderAccess(String userId, String orderCustomerId) {
        if (!String.valueOf(orderCustomerId).equals(String.valueOf(userId))) {
            throw ApiException.authorization(
                    "User %s cannot access order owned by %s".formatted(userId, orderCustomerId));
        }
    }
}
