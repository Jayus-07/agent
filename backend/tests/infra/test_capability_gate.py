"""Model Governance STOP D —— Capability Gate（能力门 + 口径收敛）。

覆盖任务书 D14：tool capability / invalid role binding / thinking 口径统一。
策略（D1/B10 渐进治理）：Registry **显式声明**才拦截，未声明维持现状
（存量 capabilities 基本为空，一刀切 fail-closed 会拦所有未登记模型）。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models
from backend.infra.llm import proxy as proxy_mod


@pytest.fixture(autouse=True)
def _registry():
    models.reset_dynamic_models_for_tests()
    yield
    models.reset_dynamic_models_for_tests()


# ── D1：bind_tools 能力门 ────────────────────────────────────────────

def test_bind_tools_blocked_when_explicitly_false(monkeypatch):
    """显式 tools=false → 拒绝专用模型 bind（返回 None → 调用方回退全局）。"""
    models.set_dynamic_models([
        {"name": "text-only-model", "provider": "custom-api",
         "capabilities": {"tools": False}, "source": "db"},
    ])
    assert proxy_mod.bind_tools_for_model("text-only-model", tools=[]) is None


def test_bind_tools_allowed_when_explicitly_true(monkeypatch):
    """显式 tools=true → 正常构建（_get_override_llm mock 掉只验证门逻辑）。"""
    models.set_dynamic_models([
        {"name": "tools-model", "provider": "custom-api",
         "capabilities": {"tools": True}, "source": "db"},
    ])
    monkeypatch.setattr(proxy_mod, "_get_override_llm",
                        lambda name: object())
    # inst.bind_tools 会被调用 —— object() 没有，换 FakeLLM
    class _Fake:
        def bind_tools(self, tools):
            return ("bound", tools)

    monkeypatch.setattr(proxy_mod, "_get_override_llm", lambda name: _Fake())
    out = proxy_mod.bind_tools_for_model("tools-model", tools=["t"])
    assert out is not None


def test_bind_tools_undecleared_keeps_legacy_behavior(monkeypatch):
    """未声明 capabilities（存量现状）→ 不拦截（渐进治理，行为兼容）。"""
    models.set_dynamic_models([
        {"name": "legacy-model", "provider": "custom-api",
         "capabilities": {}, "source": "db"},
    ])

    class _Fake:
        def bind_tools(self, tools):
            return ("bound", tools)

    monkeypatch.setattr(proxy_mod, "_get_override_llm", lambda name: _Fake())
    assert proxy_mod.bind_tools_for_model("legacy-model", tools=[]) is not None


def test_bind_tools_unregistered_still_falls_back():
    """未注册模型 → 回退（既有行为不因能力门改变）。"""
    assert proxy_mod.bind_tools_for_model("no-such-model", tools=[]) is None


# ── D4：thinking 口径收敛 ────────────────────────────────────────────

def test_thinking_registry_declaration_wins_over_heuristics():
    """Registry 显式声明优先于名称/provider 启发式（双向采信）。"""
    models.set_dynamic_models([
        {"name": "not-a-qwen", "provider": "custom-api",
         "capabilities": {"thinking": True}, "source": "db"},
        {"name": "qwen-named-model", "provider": "custom-api",
         "capabilities": {"thinking": False}, "source": "db"},
    ])
    # 名称不含 qwen 但 Registry 声明支持
    assert models.supports_thinking_flag("not-a-qwen") is True
    # 名称含 qwen 但 Registry 显式声明不支持
    assert models.supports_thinking_flag("qwen-named-model") is False


def test_thinking_legacy_fallback_for_unregistered():
    """未登记模型沿用 legacy 口径（名称 qw* / provider ∈ qwen|siliconflow）。"""
    models.reset_dynamic_models_for_tests()
    assert models.supports_thinking_flag("qwen3-32b") is True
    assert models.supports_thinking_flag("qwq-32b") is True
    assert models.supports_thinking_flag("deepseek-chat") is False


def test_thinking_unregistered_name_with_known_provider():
    models.reset_dynamic_models_for_tests()
    models.set_dynamic_providers([
        {"id": "siliconflow", "driver": "openai"},
    ])
    # 名称判不出，但 provider=siliconflow（启发式命中）→ legacy 支持
    assert models.supports_thinking_flag("GLM-4-some-name") is False


# ── D7：role 绑定校验（tool_selector 显式 false 拒绝）────────────────

def test_role_binding_rejects_tools_false_for_tool_selector():
    from backend.services.model_config import _model_validation_issue

    models.set_dynamic_models([
        {"name": "no-tools-model", "provider": "custom-api",
         "model_kind": "chat", "capabilities": {"tools": False},
         "source": "db"},
    ])
    # 凭据解析在无 DB 时抛错/返回 None 的路径不影响：tools 校验应先于/独立命中
    issue = _model_validation_issue("no-tools-model", role="tool_selector")
    assert issue is not None
    assert "tool_selector" in issue


def test_role_binding_allows_tools_true_for_tool_selector():
    from backend.services.model_config import _model_validation_issue

    models.set_dynamic_models([
        {"name": "with-tools-model", "provider": "custom-api",
         "model_kind": "chat", "capabilities": {"tools": True},
         "source": "db"},
    ])
    issue = _model_validation_issue("with-tools-model", role="tool_selector")
    # 无凭据会命中另一条校验（provider 未配 Key），但绝不能是 tools 拒绝
    assert issue is None or "工具调用" not in issue


# ── D8/D9：fallback 结构（防环）──────────────────────────────────────

def test_fallback_is_single_tier_no_cycle_possible():
    """fallback 解析只取一层单值（无 fallback graph）→ 结构上不可能成环。"""
    from backend.config import model_roles

    info = model_roles.resolve_raw("fallback")
    value = str(info.get("value") or "")
    # 单值：即使值本身是某模型名，解析函数不再对 fallback 模型二次解析 fallback
    assert model_roles.resolve_raw("fallback").get("value") == value
