"""裸 Prompt 收编守卫测试（2026-10-06：4 收编 + chat_fallback 断线修复）。

三个长期不变量：
  1. 逐字一致 —— YAML default 渲染结果 == 模块 legacy 常量（双源漂移立即红）；
  2. 注册表优先 —— 消费点经 prompt_service.render_sync 取模板（版本/trace/pin）；
  3. 降级等价 —— 注册表不可用时回落模块常量，内容与注册表渲染逐字节一致。
"""
import pytest

from backend.prompts.loader import load_defaults
from backend.prompts.renderer import PromptRenderer


def _default_template(key: str) -> str:
    """取指定键的 YAML default；键被 loader 跳过即失败（A11 断线守卫）。"""
    defaults = load_defaults()
    assert key in defaults, (
        f"{key} 不在 loader 结果中——registry 缺注册项或 YAML 校验失败"
        f"（启动会告警并跳过，运行时将恒走降级常量）"
    )
    return defaults[key]


def _render(key: str, **variables: str) -> str:
    return PromptRenderer.render(_default_template(key), variables)


# ── 不变量 1：逐字一致 ─────────────────────────────────────────

class TestByteExactness:
    def test_tool_selector_matches_constant(self):
        from backend.orchestration.graph import tool_selector

        assert _render("router.tool_selector") == tool_selector._SYSTEM_PROMPT

    def test_general_chat_matches_constant(self):
        from backend.orchestration.graph import general_chat_node

        assert _render("general_chat.system") == general_chat_node._SYSTEM_PROMPT

    def test_travel_llm_intent_matches_constant(self):
        from backend.travel.services import llm_intent_service

        assert _render("travel.llm_intent") == llm_intent_service._SYSTEM_PROMPT

    def test_chat_fallback_matches_constant(self):
        from backend.customer_service import chat_fallback

        assert _render("customer_service.chat_fallback") == (
            chat_fallback.PERSONA_SYSTEM_PROMPT
        )

    def test_followup_rewrite_renders_legacy_bytes(self):
        # 旧 _llm_rewrite 内联 f-string 构造（收编前行为），渲染必须逐字节一致
        last, ctx_snapshot, raw = "上轮发言", '{"cities": ["大理"]}', "第二天呢"
        expected = (
            "你是 query 改写器。把依赖上下文的用户问题改写成独立完整的问题。\n"
            f"上一轮用户发言：{last}\n"
            f"结构化上下文：{ctx_snapshot}\n"
            f"当前问题：{raw}\n"
            "只输出 JSON，不要输出其他内容：\n"
            '{"standalone_query": "", "resolved": true, "used_context": [],'
            ' "need_clarification": false, "clarification_question": null}\n'
            "上下文不足以改写时 resolved=false 且 need_clarification=true。"
        )
        assert _render(
            "context.followup_rewrite",
            last_user_turn=last,
            structured_context=ctx_snapshot,
            raw_query=raw,
        ) == expected


# ── 不变量 2：注册表优先 ───────────────────────────────────────

class _FakeRenderResult:
    def __init__(self, text: str):
        self.text = text


@pytest.fixture()
def capture_render_sync(monkeypatch):
    """替换 prompt_service.render_sync，记录 key 并返回哨兵模板。"""
    from backend.prompts import service as service_mod

    calls: list[str] = []

    def fake_render_sync(key, **variables):
        calls.append(key)
        return _FakeRenderResult(f"SENTINEL::{key}")

    monkeypatch.setattr(service_mod.prompt_service, "render_sync", fake_render_sync)
    return calls


class TestRegistryFirst:
    def test_tool_selector_uses_registry(self, capture_render_sync):
        from backend.orchestration.graph import tool_selector

        assert tool_selector._selector_system_prompt() == (
            "SENTINEL::router.tool_selector"
        )
        assert capture_render_sync == ["router.tool_selector"]

    def test_general_chat_uses_registry(self, capture_render_sync):
        from backend.orchestration.graph import general_chat_node

        assert general_chat_node._chat_system_prompt() == (
            "SENTINEL::general_chat.system"
        )
        assert capture_render_sync == ["general_chat.system"]

    def test_travel_intent_uses_registry(self, capture_render_sync):
        from backend.travel.services import llm_intent_service

        assert llm_intent_service._intent_system_prompt() == (
            "SENTINEL::travel.llm_intent"
        )
        assert capture_render_sync == ["travel.llm_intent"]

    def test_chat_fallback_uses_registry(self, capture_render_sync):
        from backend.customer_service import chat_fallback

        assert chat_fallback._persona_system_prompt() == (
            "SENTINEL::customer_service.chat_fallback"
        )
        assert capture_render_sync == ["customer_service.chat_fallback"]

    def test_followup_resolver_uses_registry(self, capture_render_sync):
        from backend.orchestration.context import follow_up_resolver

        prompt = follow_up_resolver._render_rewrite_prompt("a", "b", "c")
        assert prompt == "SENTINEL::context.followup_rewrite"
        assert capture_render_sync == ["context.followup_rewrite"]


# ── 不变量 3：降级等价 ─────────────────────────────────────────

class TestDegradedFallback:
    def _broken_render_sync(self, monkeypatch):
        from backend.prompts import service as service_mod

        def boom(key, **variables):
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr(service_mod.prompt_service, "render_sync", boom)

    def test_tool_selector_falls_back_to_constant(self, monkeypatch):
        from backend.orchestration.graph import tool_selector

        self._broken_render_sync(monkeypatch)
        assert tool_selector._selector_system_prompt() == tool_selector._SYSTEM_PROMPT

    def test_general_chat_falls_back_to_constant(self, monkeypatch):
        from backend.orchestration.graph import general_chat_node

        self._broken_render_sync(monkeypatch)
        assert general_chat_node._chat_system_prompt() == (
            general_chat_node._SYSTEM_PROMPT
        )

    def test_travel_intent_falls_back_to_constant(self, monkeypatch):
        from backend.travel.services import llm_intent_service

        self._broken_render_sync(monkeypatch)
        assert llm_intent_service._intent_system_prompt() == (
            llm_intent_service._SYSTEM_PROMPT
        )

    def test_chat_fallback_falls_back_to_constant(self, monkeypatch):
        from backend.customer_service import chat_fallback

        self._broken_render_sync(monkeypatch)
        assert chat_fallback._persona_system_prompt() == (
            chat_fallback.PERSONA_SYSTEM_PROMPT
        )

    def test_followup_fallback_equals_registry_render(self, monkeypatch):
        from backend.orchestration.context import follow_up_resolver

        self._broken_render_sync(monkeypatch)
        last, ctx_snapshot, raw = "a", "b", "c"
        fallback = follow_up_resolver._render_rewrite_prompt(last, ctx_snapshot, raw)
        assert fallback == _render(
            "context.followup_rewrite",
            last_user_turn=last,
            structured_context=ctx_snapshot,
            raw_query=raw,
        )
