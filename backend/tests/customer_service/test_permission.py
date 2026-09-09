"""test_permission.py — PermissionChecker 单元测试"""
import pytest

from backend.customer_service.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)
from backend.customer_service.security.permission import PermissionChecker


class TestValidateUserIdentity:

    def test_valid_user_id(self):
        state = {"cs_context": {"authenticated_user_id": "42"}}
        assert PermissionChecker.validate_user_identity(state) == "42"

    def test_missing_cs_context(self):
        with pytest.raises(AuthenticationError):
            PermissionChecker.validate_user_identity({})

    def test_anonymous_user(self):
        state = {"cs_context": {"authenticated_user_id": "anonymous"}}
        with pytest.raises(AuthenticationError):
            PermissionChecker.validate_user_identity(state)

    def test_empty_user_id(self):
        state = {"cs_context": {"authenticated_user_id": ""}}
        with pytest.raises(AuthenticationError):
            PermissionChecker.validate_user_identity(state)

    def test_none_user_id(self):
        state = {"cs_context": {"authenticated_user_id": None}}
        with pytest.raises(AuthenticationError):
            PermissionChecker.validate_user_identity(state)


class TestValidateOrderId:

    def test_valid_numeric(self):
        assert PermissionChecker.validate_order_id("12345") == "12345"

    def test_valid_alphanumeric(self):
        assert PermissionChecker.validate_order_id("ORD-2026-001") == "ORD-2026-001"

    def test_empty_string(self):
        with pytest.raises(ValidationError):
            PermissionChecker.validate_order_id("")

    def test_sql_injection(self):
        with pytest.raises(ValidationError):
            PermissionChecker.validate_order_id("1; DROP TABLE orders")

    def test_too_long(self):
        with pytest.raises(ValidationError):
            PermissionChecker.validate_order_id("a" * 65)

    def test_special_chars(self):
        with pytest.raises(ValidationError):
            PermissionChecker.validate_order_id("order_123")


class TestCheckOrderAccess:

    def test_matching_ownership(self):
        order = {"customer_id": 42, "id": 1}
        result = PermissionChecker.check_order_access("42", order)
        assert result["id"] == 1

    def test_mismatched_ownership(self):
        order = {"customer_id": 99, "id": 1}
        with pytest.raises(AuthorizationError):
            PermissionChecker.check_order_access("42", order)

    def test_string_vs_int_comparison(self):
        order = {"customer_id": "42", "id": 1}
        result = PermissionChecker.check_order_access("42", order)
        assert result["id"] == 1

    def test_missing_customer_id(self):
        order = {"id": 1}
        with pytest.raises(AuthorizationError):
            PermissionChecker.check_order_access("42", order)
