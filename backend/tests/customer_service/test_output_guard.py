"""test_output_guard.py — CSOutputGuard 三层过滤测试"""
import pytest

from backend.customer_service.security.output_guard import CSOutputGuard, get_output_guard


@pytest.fixture
def guard():
    return CSOutputGuard()


class TestRemoveInternalInfo:

    def test_clean_text_passes_through(self, guard):
        result = guard.check("您的订单已发货，预计明天到达。")
        assert not result.filtered
        assert result.text == "您的订单已发货，预计明天到达。"

    def test_sql_select_filtered(self, guard):
        result = guard.check("查询结果: SELECT id, name FROM users 显示如下")
        assert result.filtered
        assert "SELECT" not in result.text
        assert "sql_statement" in result.reasons

    def test_sql_insert_filtered(self, guard):
        result = guard.check("执行 INSERT INTO orders 成功")
        assert result.filtered
        assert "INSERT" not in result.text

    def test_internal_path_filtered(self, guard):
        result = guard.check("错误发生在 /backend/sql/executor.py 文件中")
        assert result.filtered
        assert ".py" not in result.text
        assert "internal_path" in result.reasons

    def test_stack_trace_filtered(self, guard):
        result = guard.check("Traceback (most recent call last): ...")
        assert result.filtered
        assert "Traceback" not in result.text
        assert "stack_trace" in result.reasons

    def test_secret_filtered(self, guard):
        result = guard.check("API_KEY = sk-1234567890")
        assert result.filtered
        assert "sk-1234567890" not in result.text
        assert "secret" in result.reasons

    def test_html_comment_filtered(self, guard):
        result = guard.check("回答内容<!-- 内部注释 -->结束")
        assert result.filtered
        assert "<!--" not in result.text
        assert "html_comment" in result.reasons

    def test_file_line_reference_filtered(self, guard):
        result = guard.check('File "backend/sql/executor.py", line 42')
        assert result.filtered
        assert any(r in result.reasons for r in ("stack_trace", "internal_path"))


class TestRemoveUncommittedPromises:

    def test_guarantee_promise_filtered(self, guard):
        result = guard.check("我们一定会为您处理这个问题")
        assert result.filtered
        assert "unauthorized_promise" in result.reasons

    def test_compensation_amount_filtered(self, guard):
        result = guard.check("我们将赔偿您50元")
        assert result.filtered
        assert "compensation_promise" in result.reasons

    def test_refund_amount_filtered(self, guard):
        result = guard.check("退款200元至您的账户")
        assert result.filtered
        assert "compensation_promise" in result.reasons

    def test_normal_response_passes(self, guard):
        result = guard.check("您的订单正在处理中，请耐心等待。")
        assert not result.filtered


class TestMaskOtherUserInfo:

    def test_other_user_id_masked(self, guard):
        cs_context = {
            "authenticated_user_id": "42",
            "known_other_user_ids": ["99"],
        }
        result = guard.check("用户99的订单信息", cs_context)
        assert result.filtered
        assert "99" not in result.text
        assert "other_user_info" in result.reasons

    def test_own_user_id_not_masked(self, guard):
        cs_context = {
            "authenticated_user_id": "42",
            "known_other_user_ids": ["99"],
        }
        result = guard.check("您的用户ID是42", cs_context)
        assert "42" in result.text

    def test_no_context_no_masking(self, guard):
        result = guard.check("用户99的订单信息")
        assert "99" in result.text


class TestSingleton:

    def test_get_output_guard_returns_same_instance(self):
        g1 = get_output_guard()
        g2 = get_output_guard()
        assert g1 is g2


class TestEmptyInput:

    def test_empty_string(self, guard):
        result = guard.check("")
        assert not result.filtered
        assert result.text == ""

    def test_none_like(self, guard):
        result = guard.check("")
        assert result.text == ""
