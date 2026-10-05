"""客服域 LLM 调用必须通过 PromptService 使用可治理的 Prompt Key。"""
from __future__ import annotations

from types import SimpleNamespace


EXPECTED_KEYS = {
    "customer_service.query_intent",
    "customer_service.complaint_assess",
    "customer_service.supervisor",
    "customer_service.redirect_main",
}


def test_customer_service_prompt_keys_are_registered_and_have_defaults():
    from backend.prompts.loader import load_defaults
    from backend.prompts.registry import PROMPT_REGISTRY

    assert EXPECTED_KEYS <= PROMPT_REGISTRY.keys()
    defaults = load_defaults()
    assert EXPECTED_KEYS <= defaults.keys()


def test_query_llm_decompose_uses_prompt_service(monkeypatch):
    from backend.config import customer_service as config
    from backend.customer_service.experts import query
    import backend.infra.llm.proxy as llm_proxy

    monkeypatch.setattr(config, "CS_QUERY_LLM_DECOMPOSE_ENABLED", True)
    monkeypatch.setattr(config, "CS_QUERY_LLM_TIMEOUT_MS", 1000)
    calls: list[tuple[str, dict]] = []

    # G7 收编后生产路径走 llm 代理；_LLMProxy.__getattr__ 全委托——
    # 连属性访问都会触发真实模型解析，mock 必须打在 _resolve_active_llm
    class _FakeLLM:
        def invoke(self, messages):
            assert messages[0].content == "rendered-query-prompt"
            return SimpleNamespace(content='["t_order_status"]')

    monkeypatch.setattr(llm_proxy, "_resolve_active_llm", lambda: _FakeLLM())
    monkeypatch.setattr(
        query,
        "render_prompt",
        lambda key, **variables: calls.append((key, variables))
        or "rendered-query-prompt",
        raising=False,
    )

    assert query._llm_decompose_intents("查看订单和物流") == ["t_order_status"]
    assert calls == [("customer_service.query_intent", {"question": "查看订单和物流"})]


def test_complaint_llm_assess_uses_prompt_service(monkeypatch):
    from backend.customer_service.service import complaint_service
    import backend.infra.llm.proxy as llm_proxy

    calls: list[tuple[str, dict]] = []

    class _FakeLLM:
        def invoke(self, messages):
            assert messages[0].content == "rendered-complaint-prompt"
            return SimpleNamespace(
                content='{"is_complaint": true, "severity": "high"}'
            )

    monkeypatch.setattr(llm_proxy, "_resolve_active_llm", lambda: _FakeLLM())
    monkeypatch.setattr(
        complaint_service,
        "render_prompt",
        lambda key, **variables: calls.append((key, variables))
        or "rendered-complaint-prompt",
        raising=False,
    )

    result = complaint_service.ComplaintService()._llm_assess("服务太差了")

    assert result == {"is_complaint": True, "severity": "high"}
    assert calls == [
        ("customer_service.complaint_assess", {"query": "服务太差了"})
    ]


def test_supervisor_llm_decision_uses_prompt_service(monkeypatch):
    from backend.config import customer_service as config
    from backend.customer_service import supervisor
    import backend.infra.llm.proxy as llm_proxy

    monkeypatch.setattr(config, "CS_SUPERVISOR_LLM_ENABLED", True)
    monkeypatch.setattr(config, "CS_SUPERVISOR_LLM_TIMEOUT_MS", 1000)
    calls: list[tuple[str, dict]] = []

    class _FakeLLM:
        def invoke(self, messages):
            assert messages[0].content == "rendered-supervisor-prompt"
            return SimpleNamespace(content="query")

    monkeypatch.setattr(llm_proxy, "_resolve_active_llm", lambda: _FakeLLM())
    monkeypatch.setattr(
        supervisor,
        "render_prompt",
        lambda key, **variables: calls.append((key, variables))
        or "rendered-supervisor-prompt",
        raising=False,
    )

    result = supervisor._llm_decision(
        {
            "user_message": "查订单",
            "cs_route": {"intent": "t_order_status"},
            "expert_history": [{"expert": "knowledge"}],
        }
    )

    assert result is not None
    assert result["next_expert"] == "query"
    assert calls[0][0] == "customer_service.supervisor"


def test_non_cs_detector_uses_prompt_service(monkeypatch):
    from backend.config import customer_service as config
    from backend.customer_service.analyzer import non_cs_detector as detector

    monkeypatch.setenv(config.ENV_CS_REDIRECT_MAIN_LLM_ENABLED, "true")
    calls: list[tuple[str, dict]] = []

    class FakeLLM:
        def invoke(self, messages):
            assert messages[0].content == "rendered-redirect-prompt"
            return SimpleNamespace(
                content=(
                    '{"is_non_cs": true, "confidence": 0.9, '
                    '"target_domain": "travel", "reason": "旅游规划"}'
                )
            )

    monkeypatch.setattr(detector, "_get_llm", lambda: FakeLLM())
    monkeypatch.setattr(
        detector,
        "render_prompt",
        lambda key, **variables: calls.append((key, variables))
        or "rendered-redirect-prompt",
        raising=False,
    )

    result = detector.detect_non_cs_cached("帮我规划东京五日游")

    assert result is not None
    assert result.is_non_cs is True
    assert calls == [
        ("customer_service.redirect_main", {"query": "帮我规划东京五日游"})
    ]
