"""B1-B LLM 调用治理收口定向测试（travel llm_intent + eval judge + 评测租户隔离）

对应第四阶段审计 P0-2 的 Travel + Eval 部分（rag multi_query 归 RAG 优化线，
本文件不覆盖）：

- proxy 新出口 ``bind_llm_for_role``：角色解析/参数绑定/无绑定 fail-fast
- 治理链：_BoundLLMProxy.invoke 发起前预算预占、调用后计量（mock 捕获，
  真实链路证据见交付报告验收脚本）
- travel llm_intent：走治理出口（main 角色 + temperature=0/max_tokens=1024），
  角色无绑定回落词表（既有降级语义不变）
- eval judge：走 eval_gen 角色；未配置 → total=0.0 明确失败（契约不变）；
  正常路径 evaluator 统计不退化
- 评测计费主体：tenant="eval" 与生产租户隔离，且与 llm_usage 归因
  （current_usage_attribution）同一 ContextVar 来源——三方可对账的前提
"""
from types import SimpleNamespace

import pytest

import backend.infra.llm.proxy as proxy_mod


# ─────────────────────────────────────────────────────────────
# 1. bind_llm_for_role 契约
# ─────────────────────────────────────────────────────────────


def test_bind_llm_for_role_requires_bound_model(monkeypatch):
    """角色无绑定模型 → RuntimeError（调用方按自身语义降级）。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective",
                        lambda role: {"role": role, "value": ""})
    with pytest.raises(RuntimeError, match="没有可用模型"):
        proxy_mod.bind_llm_for_role("eval_gen")


def test_bind_llm_for_role_registers_role_and_params(monkeypatch):
    """返回 _BoundLLMProxy：role/模型登记正确，temperature/max_tokens 绑定生效。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective",
                        lambda role: {"role": role, "value": "test-model"})
    captured: dict = {}

    class _FakeInner:
        def bind(self, **kwargs):
            captured["bind_kwargs"] = kwargs
            return SimpleNamespace(invoke=lambda *a, **k: None,
                                   kwargs=kwargs)

    monkeypatch.setattr(proxy_mod, "_get_override_llm",
                        lambda name: captured.setdefault("inner", _FakeInner()))
    wrapped = proxy_mod.bind_llm_for_role(
        "eval_gen", temperature=0.1, max_tokens=512)

    assert type(wrapped).__name__ == "_BoundLLMProxy"
    assert wrapped._role == "eval_gen"
    assert wrapped._model_name == "test-model"
    assert captured["bind_kwargs"] == {"temperature": 0.1, "max_tokens": 512}
    # 登记调用上下文（record_llm_result / usage 结算按此归属）
    ctx = proxy_mod.get_current_resolved_model()
    assert ctx is not None and ctx.role == "eval_gen"


def test_bound_proxy_invoke_goes_through_governance(monkeypatch):
    """invoke 走治理链：发起前预算预占 → 真实调用 → 用量计量，顺序固定。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective",
                        lambda role: {"role": role, "value": "test-model"})
    calls: list[str] = []

    class _FakeInner:
        def bind(self, **kwargs):
            return self

        def invoke(self, messages):
            calls.append("invoke")
            return SimpleNamespace(content="ok", usage_metadata={
                "input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr(proxy_mod, "_get_override_llm", lambda name: _FakeInner())
    monkeypatch.setattr(
        proxy_mod, "_call_with_resilience",
        lambda fn, *a, **k: (calls.append("resilience"), fn(*a, **k))[1])
    monkeypatch.setattr(
        proxy_mod, "_record_tokens",
        lambda result, **kw: calls.append(
            f"record:{kw.get('model_name')}"))

    wrapped = proxy_mod.bind_llm_for_role("main", temperature=0)
    resp = wrapped.invoke(["hi"])

    assert resp.content == "ok"
    # 顺序：预占/韧性链在真实调用前，计量在调用后（模型名随包装登记）
    assert calls == ["resilience", "invoke", "record:test-model"], calls


# ─────────────────────────────────────────────────────────────
# 2. travel llm_intent 治理化
# ─────────────────────────────────────────────────────────────


def _enable_intent(monkeypatch):
    from backend.config import travel as T

    monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)


def test_intent_uses_governed_entry_with_main_role(monkeypatch):
    """classify_intent_llm 经 bind_llm_for_role("main") 且带确定性参数。"""
    _enable_intent(monkeypatch)
    from backend.travel.services import llm_intent_service as svc

    captured: dict = {}

    def fake_bind(role, *, temperature=None, max_tokens=None):
        captured["role"] = role
        captured["temperature"] = temperature
        captured["max_tokens"] = max_tokens
        return SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(
                content='{"family": "query"}'))

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", fake_bind)
    got = svc.classify_intent_llm("泉州有什么特产", has_itinerary=True)
    assert got == "query_dynamic"
    assert captured == {"role": "main", "temperature": 0, "max_tokens": 1024}


def test_intent_falls_back_when_role_unbound(monkeypatch):
    """main 角色无模型绑定（RuntimeError）→ 回落词表返回 None（降级语义不变）。"""
    _enable_intent(monkeypatch)
    from backend.travel.services import llm_intent_service as svc

    def boom(role, **_kw):
        raise RuntimeError("模型角色 'main' 没有可用模型")

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", boom)
    assert svc.classify_intent_llm("随便", has_itinerary=True) is None


# ─────────────────────────────────────────────────────────────
# 3. eval judge 治理化与租户隔离
# ─────────────────────────────────────────────────────────────


def test_judge_uses_eval_gen_role(monkeypatch):
    """judge 走 eval_gen 角色治理出口，不再用 get_llm() 生产裸实例。"""
    from backend.evaluation import judge as judge_mod

    captured: dict = {}

    def fake_bind(role, **_kw):
        captured["role"] = role
        return SimpleNamespace(invoke=lambda messages: SimpleNamespace(
            content='{"scores": {"completeness": 4, "faithfulness": 5, '
                    '"conciseness": 4, "citation_quality": 3}, '
                    '"total": 4.0, "reasoning": "ok", "confidence": "medium"}',
            usage_metadata={"input_tokens": 20, "output_tokens": 8}))

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", fake_bind)
    from backend.evaluation.generation import get_evaluator_token_usage
    before = get_evaluator_token_usage()["llm_calls"]

    result = judge_mod.judge_answer("q", {"completeness": "x"}, "answer")
    assert captured["role"] == "eval_gen"
    assert result.total == 4.0
    # evaluator 内存统计不退化
    assert get_evaluator_token_usage()["llm_calls"] == before + 1


def test_judge_unconfigured_eval_gen_fails_explicitly(monkeypatch):
    """eval_gen 未绑定 → RuntimeError → judge_answer total=0.0（明确失败，
    不误判为通过；与既有异常契约一致）。"""
    from backend.evaluation import judge as judge_mod

    def boom(role, **_kw):
        raise RuntimeError("模型角色 'eval_gen' 没有可用模型")

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", boom)
    result = judge_mod.judge_answer("q", {"completeness": "x"}, "answer")
    assert result.total == 0.0
    assert "failed" in result.reasoning.lower()


def test_eval_tenant_isolated_from_production(monkeypatch):
    """评测计费主体：tenant="eval" 专用租户；归因 ContextVar 与预算绑定
    同源——current_usage_attribution 读到的 tenant/user 即预算账本主体。"""
    from backend.core.request_context import (
        get_tool_tenant_id,
        get_tool_user_id,
        set_tool_tenant_id,
        set_tool_user_id,
    )
    from backend.evaluation.service import EVAL_TENANT_ID

    assert EVAL_TENANT_ID != "default"
    assert EVAL_TENANT_ID == "eval"

    set_tool_tenant_id(EVAL_TENANT_ID)
    set_tool_user_id("eval-runner")
    try:
        assert get_tool_tenant_id() == "eval"
        assert get_tool_user_id() == "eval-runner"
        from backend.observability.llm_usage_store import (
            current_usage_attribution,
        )

        attr = current_usage_attribution()
        assert attr["tenant_id"] == "eval"
        assert attr["user_id"] == "eval-runner"
    finally:
        set_tool_tenant_id("")
        set_tool_user_id("")
