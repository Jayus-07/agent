"""test_errors.py — 客服错误分类体系测试"""
import pytest

from backend.customer_service.errors import (
    ERROR_USER_MESSAGES,
    ActionExecutionError,
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    CustomerServiceError,
    DatabaseError,
    ExternalServiceError,
    HumanHandoffError,
    OrderNotEligibleError,
    OrderNotFoundError,
    RetrievalError,
    ValidationError,
)


class TestCustomerServiceErrorBase:
    def test_default_fields(self):
        err = CustomerServiceError("boom")
        assert err.message == "boom"
        assert err.code == "CS_ERROR"
        assert err.user_message == "系统繁忙，请稍后重试"
        assert err.conversation_id is None
        assert err.trace_id is None
        assert err.retryable is False

    def test_custom_fields(self, sample_conversation_id, sample_trace_id):
        err = CustomerServiceError(
            "boom",
            code="CUSTOM",
            user_message="自定义提示",
            conversation_id=sample_conversation_id,
            trace_id=sample_trace_id,
        )
        assert err.code == "CUSTOM"
        assert err.user_message == "自定义提示"
        assert err.conversation_id == sample_conversation_id
        assert err.trace_id == sample_trace_id

    def test_is_exception(self):
        assert issubclass(CustomerServiceError, Exception)

    def test_str_contains_message(self):
        err = CustomerServiceError("something broke")
        assert "something broke" in str(err)


class TestAuthErrors:
    def test_authentication_error(self):
        err = AuthenticationError()
        assert err.code == "AUTH_FAILED"
        assert err.user_message == "请先登录后再使用此功能"
        assert err.retryable is False

    def test_authorization_error(self):
        err = AuthorizationError()
        assert err.code == "PERMISSION_DENIED"
        assert err.user_message == "您没有权限执行此操作"

    def test_authentication_inherits_base(self):
        err = AuthenticationError()
        assert isinstance(err, CustomerServiceError)


class TestValidationErrors:
    def test_validation_error(self):
        err = ValidationError()
        assert err.code == "VALIDATION_ERROR"
        assert err.retryable is False


class TestBusinessErrors:
    def test_business_rule_error(self):
        err = BusinessRuleError("余额不足")
        assert err.code == "BUSINESS_RULE"
        assert err.user_message == "余额不足"

    def test_business_rule_custom_user_message(self):
        err = BusinessRuleError("余额不足", user_message="账户余额不足，无法退款")
        assert err.user_message == "账户余额不足，无法退款"

    def test_order_not_found(self):
        err = OrderNotFoundError()
        assert err.code == "ORDER_NOT_FOUND"
        assert err.user_message == "未找到相关订单信息"
        assert isinstance(err, BusinessRuleError)

    def test_order_not_eligible(self):
        err = OrderNotEligibleError("订单已超过退货期限")
        assert err.code == "ORDER_NOT_ELIGIBLE"
        assert isinstance(err, BusinessRuleError)


class TestRetryableErrors:
    @pytest.mark.parametrize("error_cls,expected_code", [
        (RetrievalError, "RETRIEVAL_ERROR"),
        (ExternalServiceError, "EXTERNAL_SERVICE"),
        (DatabaseError, "DATABASE_ERROR"),
        (HumanHandoffError, "HANDOFF_ERROR"),
    ])
    def test_retryable_flag(self, error_cls, expected_code):
        err = error_cls()
        assert err.retryable is True
        assert err.code == expected_code

    def test_retrieval_default_message(self):
        err = RetrievalError()
        assert err.user_message == "暂时无法查询相关信息"


class TestActionErrors:
    def test_action_execution_error(self):
        err = ActionExecutionError("支付网关超时")
        assert err.code == "ACTION_FAILED"
        assert err.user_message == "操作执行失败，请稍后重试"

    def test_action_execution_custom_user_message(self):
        err = ActionExecutionError("timeout", user_message="支付超时，请重试")
        assert err.user_message == "支付超时，请重试"


class TestErrorUserMessagesMap:
    def test_all_codes_have_mapping(self):
        expected_codes = {
            "AUTH_FAILED", "PERMISSION_DENIED", "VALIDATION_ERROR",
            "ORDER_NOT_FOUND", "BUSINESS_RULE", "RETRIEVAL_ERROR",
            "EXTERNAL_SERVICE", "DATABASE_ERROR", "ACTION_FAILED",
            "HANDOFF_ERROR",
        }
        assert expected_codes == set(ERROR_USER_MESSAGES.keys())

    def test_business_rule_maps_to_none(self):
        assert ERROR_USER_MESSAGES["BUSINESS_RULE"] is None
