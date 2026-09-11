package com.agent.cs.statemachine;

import com.agent.cs.common.ApiException;

/**
 * 状态机异常工厂（对齐 InvalidTransitionError / InvariantViolationError 语义）
 */
final class ApiExceptionFactory {

    private ApiExceptionFactory() {
    }

    static ApiException invalidTransition(String dimension, String src, String dst) {
        return ApiException.businessRule("Invalid %s transition: %s → %s".formatted(dimension, src, dst));
    }
}
