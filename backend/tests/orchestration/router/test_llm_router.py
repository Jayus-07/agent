"""LLM 路由的候选约束、非置信分与故障澄清回归。"""
from types import SimpleNamespace

from backend.orchestration.router.llm_router import LLMRouter
from backend.orchestration.router.types import ExecutionMode


class _Bound:
    def __init__(self, raw):
        self.raw = raw
        self.prompt = ""

    def invoke(self, input=None, **_kwargs):
        self.prompt = input[0]["content"]
        return self.raw


class _LLM:
    def __init__(self, raw):
        self.bound = _Bound(raw)

    def bind(self, **_kwargs):
        return self.bound


def _wire(monkeypatch, raw):
    import backend.infra.llm as llm_module
    import backend.infra.timeout as timeout_module
    import backend.prompts.service as prompt_module

    fake = _LLM(raw)
    monkeypatch.setattr(llm_module, "llm", fake)
    monkeypatch.setattr(
        timeout_module,
        "safe_call_with_timeout",
        lambda fn, **kwargs: fn(kwargs.get("input")),
    )
    monkeypatch.setattr(
        prompt_module.prompt_service,
        "render_sync",
        lambda *_args, **_kwargs: SimpleNamespace(text="旧 active prompt 未列出候选"),
    )
    return fake


def test_dynamic_candidate_and_forged_score_cannot_authorize_fast_path(monkeypatch):
    raw = SimpleNamespace(content=(
        '{"candidate":"rag.search","score":0.999,"confidence":1.0,'
        '"execution_mode":"direct","reason":"测试"}'
    ))
    fake = _wire(monkeypatch, raw)

    decision = LLMRouter(timeout=1).route(
        "查询退款制度", domain="knowledge", allowed_candidates=["rag.search"],
    )

    assert "rag.search" in fake.bound.prompt
    assert "web.search" not in fake.bound.prompt
    assert decision.route_mode == "llm_selection"
    assert decision.execution_mode is ExecutionMode.PLAN
    assert decision.confidence == 0.0
    assert [item.name for item in decision.candidates] == ["rag.search"]
    assert decision.candidates[0].score == 0.0
    assert decision.routing_meta["confidence_used"] is False


def test_unregistered_or_wrong_domain_candidate_is_rejected(monkeypatch):
    raw = SimpleNamespace(content='{"candidate":"sql.query","score":1.0}')
    _wire(monkeypatch, raw)

    decision = LLMRouter(timeout=1).route(
        "查询退款制度", domain="knowledge",
    )

    assert decision.route_mode == "clarify"
    assert decision.candidates == []
    assert decision.routing_meta["decision_source"] == "llm_failure"
    assert decision.routing_meta["fallback_reason"] == "invalid_or_unregistered_candidate"


def test_timeout_and_invalid_json_have_no_rag_or_other_candidate(monkeypatch):
    import backend.infra.timeout as timeout_module

    raw = SimpleNamespace(content="not json")
    _wire(monkeypatch, raw)
    monkeypatch.setattr(timeout_module, "safe_call_with_timeout", lambda *_a, **_k: None)
    timeout = LLMRouter(timeout=1).route("未知问题", domain="knowledge")
    assert timeout.route_mode == "clarify"
    assert timeout.candidates == []
    assert timeout.routing_meta["fallback_reason"] == "timeout"

    monkeypatch.setattr(timeout_module, "safe_call_with_timeout", lambda *_a, **_k: raw)
    malformed = LLMRouter(timeout=1).route("未知问题", domain="knowledge")
    assert malformed.route_mode == "clarify"
    assert malformed.candidates == []
    assert malformed.routing_meta["fallback_reason"] == "parse_failed"


def test_unknown_domain_does_not_default_to_rag(monkeypatch):
    fake = _wire(monkeypatch, SimpleNamespace(content='{"candidate":"rag.search"}'))

    decision = LLMRouter(timeout=1).route("无法识别", domain="unknown")

    assert fake.bound.prompt == ""
    assert decision.route_mode == "clarify"
    assert decision.candidates == []
