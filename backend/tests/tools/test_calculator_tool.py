"""tests/tools/test_calculator_tool.py — 计算工具测试

覆盖:
1. 基础四则/幂/整除/取模
2. 白名单函数与常量
3. 非白名单语法/函数/变量拒绝（无注入面）
4. 边界：除零、幂指数过大、超长表达式
5. Registry 注册
"""
import pytest

from backend.tools.calculator import _UnsafeExpression, evaluate_expression


class TestEvaluate:
    def test_basic_arithmetic(self):
        assert evaluate_expression("1+2*3") == 7
        assert evaluate_expression("(1+2)*3") == 9
        assert evaluate_expression("10/4") == pytest.approx(2.5)
        assert evaluate_expression("10//3") == 3
        assert evaluate_expression("10%3") == 1
        assert evaluate_expression("2**10") == 1024
        assert evaluate_expression("-5+3") == -2

    def test_functions(self):
        assert evaluate_expression("abs(-3.5)") == 3.5
        assert evaluate_expression("round(0.8754, 2)") == 0.88
        assert evaluate_expression("max(1, 5, 3)") == 5
        assert evaluate_expression("sqrt(4)") == 2.0
        assert evaluate_expression("floor(2.7)") == 2
        assert evaluate_expression("pow(2, 8)") == 256

    def test_constants(self):
        assert evaluate_expression("pi") == pytest.approx(3.14159265)
        assert evaluate_expression("round(e, 2)") == pytest.approx(2.72)

    def test_rejects_non_whitelisted(self):
        # 函数调用白名单外
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("__import__('os').system('ls')")
        # 属性访问 / 变量
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("x + 1")
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("(lambda: 1)()")
        # 字符串/布尔常量
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("'a'")
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("True")
        # 下标/比较等语法
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("1 if 2 else 3")

    def test_rejects_syntax_error_and_long(self):
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("1++*2")
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("1+" * 300 + "1")

    def test_division_by_zero_and_huge_pow(self):
        with pytest.raises(ZeroDivisionError):
            evaluate_expression("1/0")
        with pytest.raises(_UnsafeExpression):
            evaluate_expression("2**99999")


class TestToolInvoke:
    def test_tool_registered(self):
        from backend.tools.tool_registry import tool_registry
        assert "calculate_tool" in tool_registry.tool_names

    def test_invoke_success(self):
        from backend.tools.calculator import calculate_tool
        out = calculate_tool.invoke({"expression": "(1250/8900)*100"})
        assert "= " in out and "14.0449438202" in out

    def test_invoke_int_result_no_decimal_tail(self):
        from backend.tools.calculator import calculate_tool
        assert calculate_tool.invoke({"expression": "2**10"}) == "2**10 = 1024"

    def test_invoke_error_message(self):
        from backend.tools.calculator import calculate_tool
        out = calculate_tool.invoke({"expression": "__import__('os')"})
        assert out.startswith("❌")
