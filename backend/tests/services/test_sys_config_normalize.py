"""test_sys_config_normalize.py — sys_config 通用值校验（2026-09-19 扩展）

`_normalize_with` 是 2026-09-19 为模型角色扩展出来的通用校验：支持静态白名单
之外的「函数式校验」与「大小写敏感」。守卫开关（_SWITCHES）仍走原路径，行为必须
与历史完全一致 —— 本文件同时锁住这两件事。

见 docs/model-config-governance-design.md §1.1。
"""
from __future__ import annotations

import pytest

from backend.services import sys_config
from backend.services.sys_config import _env_default, _normalize, _normalize_with


# =====================================================
# 守卫开关的既有行为（不得回归）
# =====================================================

def test_guard_switch_lowercases_and_whitelists():
    spec = sys_config._SWITCHES["JWT_SESSION_GUARD_MODE"]
    assert _normalize_with(spec, "ENFORCE") == "enforce"   # 小写归一（历史行为）
    assert _normalize_with(spec, "  audit  ") == "audit"   # strip
    assert _normalize_with(spec, "yolo") is None           # 非法 → None


def test_normalize_delegates_to_registry():
    assert _normalize("SENSITIVE_API_GUARD_MODE", "audit") == "audit"
    assert _normalize("SENSITIVE_API_GUARD_MODE", "nope") is None
    assert _normalize("NOT_REGISTERED", "audit") is None


def test_env_default_falls_back_on_invalid(monkeypatch):
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "yolo")
    assert _env_default("JWT_SESSION_GUARD_MODE") == "audit"   # 注册表缺省
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "enforce")
    assert _env_default("JWT_SESSION_GUARD_MODE") == "enforce"


# =====================================================
# 扩展点：大小写敏感
# =====================================================

_CASE_SPEC = {"allowed": ("MiniMax-M3", "qwen3.7-plus"), "default": "", "case_sensitive": True}


def test_case_sensitive_preserves_value():
    assert _normalize_with(_CASE_SPEC, "MiniMax-M3") == "MiniMax-M3"
    assert _normalize_with(_CASE_SPEC, "  MiniMax-M3 ") == "MiniMax-M3"


def test_case_sensitive_rejects_wrong_case():
    """这正是 sys_config 不能直接复用于模型名的原因：小写归一后匹配不上。"""
    assert _normalize_with(_CASE_SPEC, "minimax-m3") is None
    assert _normalize_with(_CASE_SPEC, "MINIMAX-M3") is None


def test_case_sensitive_default_is_off():
    """不显式声明时沿用守卫开关的历史行为（小写归一）。"""
    spec = {"allowed": ("audit",)}
    assert _normalize_with(spec, "AUDIT") == "audit"
    # 对照：同一输入在 case_sensitive=True 下会被拒（白名单是原大小写）
    assert _normalize_with({**spec, "case_sensitive": True}, "AUDIT") is None


# =====================================================
# 扩展点：函数式校验
# =====================================================

def test_validator_callable_used_when_present():
    spec = {"validator": lambda v: v.startswith("Qwen/"), "default": "", "case_sensitive": True}
    assert _normalize_with(spec, "Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert _normalize_with(spec, "gpt-4") is None


def test_validator_takes_precedence_over_allowed():
    """两者同时存在时 validator 优先（合法集动态变化时用 validator）。"""
    spec = {"allowed": ("never",), "validator": lambda v: v == "ok"}
    assert _normalize_with(spec, "ok") == "ok"
    assert _normalize_with(spec, "never") is None


def test_validator_receives_stripped_case_preserved_value():
    seen: list[str] = []
    spec = {"validator": lambda v: (seen.append(v), True)[1], "case_sensitive": True}
    _normalize_with(spec, "  MixedCase  ")
    assert seen == ["MixedCase"]


# =====================================================
# 边界
# =====================================================

def test_none_spec_returns_none():
    assert _normalize_with(None, "anything") is None


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_values_rejected(raw):
    spec = sys_config._SWITCHES["JWT_SESSION_GUARD_MODE"]
    assert _normalize_with(spec, raw) is None


def test_spec_without_allowed_and_validator_rejects_everything():
    """既没白名单也没校验器 → 一律拒绝（fail-closed，不放开任意值）。"""
    assert _normalize_with({"default": ""}, "anything") is None
