"""供应商级 extra_body（087 迁移）：校验与安全边界。

背景（2026-10-09 实测）：凭据层早已支持 extra_body 并透传给
ChatOpenAI(extra_body=...)，但 llm_providers 无该列，管理端无法配置
「关闭思考」。实测主模型 reasoning/completion = 0.73（73% 生成 token
花在思考上），多查询改写因 max_tokens 被思考吃光而只产出 1 个变体。

本组测试锁三件事：
  1. 合法值原样保存（thinking / enable_thinking / reasoning_effort）；
  2. 保留字段被拒（不得经此旁路顶替模型名、流式开关、工具或凭据）；
  3. 规模有界（键数/嵌套深度），避免撑大每次请求体。
"""
import pytest

from backend.services.model_config import _normalize_extra_body


# ── 合法输入 ──────────────────────────────────────────

@pytest.mark.parametrize("value", [
    {},                                          # 空 = 不附加
    {"thinking": {"type": "disabled"}},          # 火山方舟
    {"enable_thinking": False},                  # 通义
    {"reasoning_effort": "low"},                 # OpenAI 系
    {"thinking": {"type": "disabled"}, "top_p": 0.9},
])
def test_valid_values_preserved(value):
    assert _normalize_extra_body(value) == value


def test_none_becomes_empty():
    """None（未提交）与 {}（显式清空）都归一为空对象。"""
    assert _normalize_extra_body(None) == {}


def test_scalar_values_kept_as_is():
    """False/数字等非字符串标量不得被 str() 化（会变成 'False' 传给上游）。"""
    out = _normalize_extra_body({"enable_thinking": False, "top_k": 5})
    assert out == {"enable_thinking": False, "top_k": 5}
    assert out["enable_thinking"] is False


# ── 保留字段必须被拒 ──────────────────────────────────

@pytest.mark.parametrize("key", [
    "model", "messages", "stream", "input", "prompt",
    "tools", "tool_choice", "functions", "function_call",
    "api_key", "authorization",
])
def test_reserved_keys_rejected(key):
    with pytest.raises(ValueError, match="保留字段"):
        _normalize_extra_body({key: "x"})


def test_reserved_key_case_insensitive():
    """大小写变体同样拦截（Stream / MODEL）。"""
    with pytest.raises(ValueError, match="保留字段"):
        _normalize_extra_body({"Stream": True})
    with pytest.raises(ValueError, match="保留字段"):
        _normalize_extra_body({"MODEL": "evil"})


# ── 规模边界 ──────────────────────────────────────────

def test_too_many_keys_rejected():
    with pytest.raises(ValueError, match="键数"):
        _normalize_extra_body({f"k{i}": 1 for i in range(21)})


def test_deep_nesting_rejected():
    with pytest.raises(ValueError, match="嵌套过深"):
        _normalize_extra_body({"a": {"b": {"c": {"d": 1}}}})


def test_moderate_nesting_allowed():
    """3 层以内允许（thinking.type 本体就是 2 层）。"""
    assert _normalize_extra_body({"a": {"b": {"c": 1}}}) == {"a": {"b": {"c": 1}}}


# ── 类型边界 ──────────────────────────────────────────

@pytest.mark.parametrize("bad", ["string", 123, ["list"], True])
def test_non_mapping_rejected(bad):
    with pytest.raises(ValueError, match="必须是对象"):
        _normalize_extra_body(bad)


def test_empty_key_rejected():
    with pytest.raises(ValueError, match="空键"):
        _normalize_extra_body({"  ": 1})
