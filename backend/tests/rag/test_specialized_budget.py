"""专项模型必须共享请求预算，缓存、失败和重试不能重复或漏记账。"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.infra.llm import budget, pricing


@pytest.fixture
def metering(monkeypatch):
    class Quota:
        def __init__(self):
            self.entries = []

        def reserve(self, **kwargs):
            entry = {"status": "reserved", **kwargs}
            self.entries.append(entry)
            return entry

        def settle(self, entry, amount):
            entry.update(status="settled", amount=amount)

        def mark_needs_review(self, entry, reason):
            entry.update(status="needs_review", reason=reason)
            return True

    quota = Quota()
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_calls=3), mode="enforce",
        user_id="eval-user", tenant_id="eval", quota_store=quota,
    )
    monkeypatch.setitem(budget._states, "specialized-budget-test", state)
    request_token = budget._current_request_id.set("specialized-budget-test")
    outer = object()
    reservation_token = budget._current_quota_reservation.set(outer)
    events = []
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=lambda event: events.append(dict(event))),
    )
    monkeypatch.setattr(
        pricing, "get_current_price_table",
        lambda model, component: pricing.PriceTable([
            pricing.PriceLine(model, component, "input", Decimal("1"), currency="CNY"),
        ]),
    )
    try:
        from backend.core.request_context import scoped_tool_identity

        with scoped_tool_identity(user_id="eval-user", tenant_id="eval"):
            yield state, quota.entries, events, outer
    finally:
        budget._current_quota_reservation.reset(reservation_token)
        budget._current_request_id.reset(request_token)


def test_embedding_cache_bills_once_and_budget_rejects_before_outbound(monkeypatch, metering):
    from backend.rag import embedding_singleton as es

    calls = []
    tracked = es._TrackedEmbedding.__new__(es._TrackedEmbedding)
    tracked._inner = SimpleNamespace(
        embed_query=lambda text: calls.append(text) or [1.0, 2.0],
    )
    tracked._provider = "cloud"
    tracked._provider_id = "test-provider"
    tracked._model_name = "test-embedding"
    tracked._refresh_if_changed = lambda: None
    monkeypatch.setattr(es, "_EMBED_PROCESS_CACHE_ENABLED", False)
    es.reset_embedding_cache()
    es._REQUEST_EMBEDS.set(None)
    es._EMBED_REQUEST_RECORD.set(None)
    es.begin_embedding_request(reset=True)
    state, entries, events, outer = metering
    try:
        tracked.embed_query("同一查询")
        tracked.embed_query("同一查询")
        assert calls == ["同一查询"]
        assert state.calls == 1
        assert entries[0]["status"] == "settled"
        assert events[0]["usage_source"] == "estimated"
        assert events[0]["cost_status"] == "estimated"
        assert events[0]["user_id"] == "eval-user"
        assert events[0]["tenant_id"] == "eval"
        assert budget._current_quota_reservation.get() is outer

        tracked.embed_query("第二查询")
        tracked.embed_query("第三查询")
        with pytest.raises(budget.RequestBudgetExceeded):
            tracked.embed_query("不可出站")
        assert len(calls) == len(events) == len(entries) == 3
    finally:
        es.reset_embedding_cache()
        es._REQUEST_EMBEDS.set(None)
        es._EMBED_REQUEST_RECORD.set(None)


def test_rerank_retry_has_separate_unknown_and_settled_entries(monkeypatch, metering):
    import requests

    from backend.rag.reranker import DashScopeReranker

    attempts = []

    def post(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.exceptions.Timeout()
        return SimpleNamespace(status_code=200, json=lambda: {
            "output": {"results": [{"index": 0, "relevance_score": 0.9}]},
            "usage": {"total_tokens": 12},
        })

    monkeypatch.setattr("backend.rag.reranker.requests.post", post)
    model = DashScopeReranker("synthetic", model="test-rerank", api_format="dashscope")
    assert model.rank("查询", ["正文"]) == [(0, 0.9)]
    state, entries, events, outer = metering
    assert state.calls == 2 and state.retries == 1
    assert [entry["status"] for entry in entries] == ["needs_review", "settled"]
    assert [event["decision"] for event in events] == ["primary", "retry"]
    assert events[0]["finish_reason"] == "error"
    assert all(event["user_id"] == "eval-user" and event["tenant_id"] == "eval" for event in events)
    assert entries[1]["amount"] == Decimal("0.000012")
    assert budget._current_quota_reservation.get() is outer


def _rerank_success_response():
    return SimpleNamespace(
        status_code=200, json=lambda: {
            "output": {"results": [{"index": 0, "relevance_score": 0.9}]},
            "usage": {"total_tokens": 12},
        },
    )


def test_rerank_unknown_price_rejects_before_outbound_in_enforce(monkeypatch, metering):
    """enforce + 无任何可信单价：Provider 出站前拒绝，不产生预占与假 usage。

    2026-10-17 价格预检契约（#85）取代旧的「缺价放行后再对账」行为：
    费用无法证明时不得发出真实请求。
    """
    from backend.rag.reranker import DashScopeReranker

    outbound = []
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *_: pricing.PriceTable([]))
    monkeypatch.setattr(
        "backend.rag.reranker.requests.post",
        lambda *_a, **_k: outbound.append(1) or _rerank_success_response(),
    )
    model = DashScopeReranker("synthetic", model="unpriced-test-model", api_format="dashscope")
    _state, entries, events, _outer = metering

    with pytest.raises(budget.RequestBudgetExceeded):
        model.rank("查询", ["正文"])

    assert outbound == [], "缺价不得产生 Provider 出站请求"
    assert entries == [], "拒绝前不得创建预占"
    assert events == [], "拒绝后不得写入成功 usage 行"


def test_rerank_price_unknown_keeps_reserved_quota_without_zero_settlement(monkeypatch, metering):
    """可出站但拿不到有效价格：预占保留并进入待对账，不按 0 元精确结算。"""
    from backend.rag.reranker import DashScopeReranker

    outbound = []
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *_: pricing.PriceTable([]))
    # 注册表仍有单价 → 硬预算门禁允许出站；但计费侧拿不到价格 → unpriced。
    monkeypatch.setattr(
        "backend.infra.llm.models.get_model_pricing", lambda model: (1.0, 2.0),
    )
    monkeypatch.setattr(pricing, "calculate_fallback_cost", lambda *_a, **_k: Decimal("0"))
    monkeypatch.setattr(
        "backend.rag.reranker.requests.post",
        lambda *_a, **_k: outbound.append(1) or _rerank_success_response(),
    )
    model = DashScopeReranker("synthetic", model="unpriced-test-model", api_format="dashscope")

    assert model.rank("查询", ["正文"]) == [(0, 0.9)]
    _state, entries, events, _outer = metering
    assert len(outbound) == 1
    assert entries[0]["status"] == "needs_review"
    assert entries[0]["amount_cny"] == Decimal("0.50")
    assert "amount" not in entries[0], "未知价格不得按确定金额结算"
    assert events[0]["cost_status"] in {"unpriced", "price_unknown"}
    assert events[0]["user_id"] == "eval-user"
    assert events[0]["tenant_id"] == "eval"


def test_rerank_uses_source_name_without_directory_or_mutating_content(monkeypatch, metering):
    from langchain_core.documents import Document

    from backend.rag.reranker import DashScopeReranker

    captured = []

    def post(*_args, **kwargs):
        captured.append(kwargs["json"]["input"]["documents"])
        return SimpleNamespace(status_code=200, json=lambda: {
            "output": {"results": [{"index": 0, "relevance_score": 0.9}]},
            "usage": {"total_tokens": 12},
        })

    monkeypatch.setattr("backend.rag.reranker.requests.post", post)
    document = Document(page_content="商家承担运费", metadata={"source": "D:/private/policy.pdf"})
    model = DashScopeReranker("synthetic", model="test-rerank", api_format="dashscope")
    assert model.compress_documents([document], "运费由谁承担") == [document]
    assert captured == [["文档：policy.pdf\n商家承担运费"]]
    assert document.page_content == "商家承担运费"
