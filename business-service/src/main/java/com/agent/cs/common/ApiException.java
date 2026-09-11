package com.agent.cs.common;

import org.springframework.http.HttpStatus;

/**
 * 业务异常体系（对齐 backend/customer_service/errors.py）
 */
public class ApiException extends RuntimeException {

    private final HttpStatus status;
    private final String userMessage;

    public ApiException(HttpStatus status, String message, String userMessage) {
        super(message);
        this.status = status;
        this.userMessage = userMessage;
    }

    public HttpStatus getStatus() { return status; }
    public String getUserMessage() { return userMessage; }

    public static ApiException businessRule(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, message, "当前状态不允许此操作");
    }

    public static ApiException notFound(String message) {
        return new ApiException(HttpStatus.NOT_FOUND, message, "资源不存在");
    }

    public static ApiException validation(String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, message, "请求参数不合法");
    }

    public static ApiException authentication(String message) {
        return new ApiException(HttpStatus.UNAUTHORIZED, message, "身份验证失败");
    }

    public static ApiException authorization(String message) {
        return new ApiException(HttpStatus.FORBIDDEN, message, "无权访问该资源");
    }
}
