"""tools/calculator.py — 精确数学计算工具（AST 白名单求值）

LLM 心算不可靠：任何涉及统计、比例、增长率、单位换算的计算都应走本工具。
实现: ast 解析 + 白名单节点求值，不 eval 原始字符串 —— 无注入面、无副作用。
"""
import ast
import math
import operator

from langchain_core.tools import tool
from backend.shared.logger import logger

_MAX_EXPR_LEN = 500
_MAX_DEPTH = 20
_MAX_POW_EXPONENT = 1000

# 二元/一元运算符白名单
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 函数白名单（只保留无副作用的数学函数）
_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "log": math.log,
    "log10": math.log10,
}

# 常量白名单
_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}


class _UnsafeExpression(Exception):
    """表达式含白名单外的语法/函数/变量"""


def _eval_node(node: ast.AST, depth: int = 0) -> float:
    if depth > _MAX_DEPTH:
        raise _UnsafeExpression("表达式嵌套过深")
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth + 1)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise _UnsafeExpression(f"不支持的常量: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise _UnsafeExpression(f"不支持的运算符: {type(node.op).__name__}")
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise _UnsafeExpression(f"幂指数过大: {right}")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise _UnsafeExpression(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.operand, depth + 1))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise _UnsafeExpression("仅支持白名单内函数的直接调用")
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise _UnsafeExpression(f"不支持的函数: {node.func.id}")
        args = [_eval_node(a, depth + 1) for a in node.args]
        return fn(*args)
    if isinstance(node, ast.Name):
        if node.id in _NAMES:
            return _NAMES[node.id]
        raise _UnsafeExpression(f"不支持的变量: {node.id}")
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, depth + 1) for e in node.elts)
    raise _UnsafeExpression(f"不支持的语法: {type(node).__name__}")


def evaluate_expression(expression: str) -> float:
    """安全求值入口（独立于 tool，便于单测）。非白名单语法抛 _UnsafeExpression。"""
    if len(expression) > _MAX_EXPR_LEN:
        raise _UnsafeExpression(f"表达式过长（>{_MAX_EXPR_LEN} 字符）")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise _UnsafeExpression(f"语法错误: {e}") from e
    result = _eval_node(tree)
    if isinstance(result, complex) or (isinstance(result, float) and math.isnan(result)):
        raise _UnsafeExpression("结果不是实数")
    return result


@tool
def calculate_tool(expression: str) -> str:
    """
    精确数学计算器（AST 白名单求值，无副作用）。
    expression: 数学表达式，支持 + - * / // % **、括号、abs/round/min/max/sum/pow/sqrt/floor/ceil/log、pi/e。
    示例: "(1250/8900)*100"、"sqrt(2)*pi"、"round(0.8754, 2)"
    适用场景：任何涉及数字计算的场景（统计、比例、增长率、换算）——不要心算。
    """
    logger.info(f"[Tool:calculate] {expression[:80]}")
    try:
        result = evaluate_expression(expression)
    except _UnsafeExpression as e:
        return f"❌ 无法计算: {e}"
    except ZeroDivisionError:
        return "❌ 无法计算: 除以零"
    except OverflowError:
        return "❌ 无法计算: 结果溢出"
    except (TypeError, ValueError) as e:
        return f"❌ 无法计算: {e}"

    # 整数结果去掉小数尾巴；浮点保留 10 位有效精度
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return f"{expression.strip()} = {int(result)}"
    return f"{expression.strip()} = {round(result, 10)}"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(calculate_tool, __file__)
