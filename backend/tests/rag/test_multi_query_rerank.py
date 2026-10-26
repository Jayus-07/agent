"""多查询 Rerank 评分契约（PR #52，Phase C）。

背景：MultiQuery 合并出的候选此前只用**单个原始 query** 重排，变体召回到的
高相关证据永远赢不了；而 2026-10-17 的"合并后直接按已有 rerank_score 排序"
实验让 Recall@5 从 0.9286 掉到 0.8810 并已回滚。本文件锁死替代方案的正确
契约：同一批候选按"原问题 + 有效改写"分别**全候选**评分，每个候选取最大值，
阈值与 top_k 只在聚合后执行一次。

所有 Provider 调用都是 Fake：单测不产生真实模型费用。
"""
from __future__ import annotations

import math
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document


@pytest.fixture(autouse=True)
def _clean_env():
    from backend.observability.tracer import trace_collector
    from backend.rag.retrieval_budget import clear_retrieval_budget

    clear_retrieval_budget()
    trace_collector.clear_for_test()
    yield
    clear_retrieval_budget()
    trace_collector.clear_for_test()


def _docs(*names: str) -> list[Document]:
    return [
        Document(page_content=f"{name}正文", metadata={
            "chunk_id": name, "source": f"{name}.pdf", "source_query": "原始",
        })
        for name in names
    ]


class _FakeBackend:
    """按 query 返回预置分数；记录每次全候选评分的入参。"""

    def __init__(self, table, *, fail=(), rows_override=None):
        self.table = table
        self.fail = set(fail)
        self.rows_override = rows_override or {}
        self.calls = []

    def rank_all(self, query, documents, *, deadline_at=None):
        self.calls.append({
            "query": query,
            "documents": list(documents),
            "count": len(documents),
            "deadline_at": deadline_at,
        })
        if query in self.fail:
            raise RuntimeError("provider unavailable")
        scores = self.table.get(query, [])
        rows = [(i, float(s)) for i, s in enumerate(scores) if i < len(documents)]
        override = self.rows_override.get(query)
        if override is not None:
            rows = override(rows)
        return rows

    def compress_documents(self, documents, query, **kwargs):  # 单查询旧路径
        self.calls.append({"query": query, "single": True})
        return list(documents)[: kwargs.get("top_k", 8)]


def _compressor(backend, *, top_k=8, threshold=0.3):
    from backend.rag.reranker import RerankCompressor

    comp = RerankCompressor()
    comp.top_k = top_k
    comp.threshold = threshold
    comp.__dict__["backend"] = backend
    comp.__dict__["_backend_type"] = "dashscope"
    return comp


def _with_queries(docs, queries):
    docs[0].metadata["_runtime_mq_queries"] = list(queries)
    return docs


def _rerank_span():
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("多查询评分", session_id="mq-rerank-test")
    trace_collector.start_span("root", parent_id=None, name="root", type="agent")
    return trace


def _span_of(trace, span_id="rerank"):
    return next(span for span in trace.spans if span.span_id == span_id)


ORIGINAL = "采购新品上架需要哪些部门协作？"
DEPT_QUERY = "采购部 质检部 法务部 在新品上架中的职责分工与审批环节"
CERT_QUERY = "新品上架所需资质证明 质检标准 合规认证要求"


class TestMaxAggregation:
    def test_variant_evidence_can_win_top1(self):
        """部门/流程视角下的采购证据（0.74）必须压过库存流程文档（0.66）。"""
        docs = _docs("库存", "采购", "质检")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY, CERT_QUERY])
        backend = _FakeBackend({
            ORIGINAL: [0.6554, 0.6068, 0.4030],
            DEPT_QUERY: [0.2229, 0.3403, 0.2500],
            CERT_QUERY: [0.6606, 0.7413, 0.5000],
        })

        out = _compressor(backend, top_k=8, threshold=0.3).compress_documents(
            docs, ORIGINAL,
        )

        assert [d.metadata["chunk_id"] for d in out][:2] == ["采购", "库存"]
        assert out[0].metadata["rerank_score"] == pytest.approx(0.7413)
        assert out[0].metadata["rerank_winner_query"] == CERT_QUERY
        # 每条查询都必须拿到全部候选（top_n = 候选数），不得只取 Top-K
        assert [call["count"] for call in backend.calls] == [3, 3, 3]
        # 既有首次召回来源不被评分覆盖
        assert out[0].metadata["source_query"] == "原始"

    def test_tie_prefers_original_query_then_input_order(self):
        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({
            ORIGINAL: [0.5000, 0.4000, 0.4000],
            DEPT_QUERY: [0.5000, 0.4000, 0.4000],
        })

        out = _compressor(backend).compress_documents(docs, ORIGINAL)

        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙", "丙"]
        assert out[0].metadata["rerank_winner_query"] == ORIGINAL
        assert out[1].metadata["rerank_winner_query"] == ORIGINAL

    def test_missing_scores_are_not_treated_as_zero_or_one(self):
        """某候选只有变体给分时取该分数；完全没有分数的候选不得进入结果。"""
        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({
            ORIGINAL: [0.4000],
            DEPT_QUERY: [0.0, 0.9000],
        })

        out = _compressor(backend).compress_documents(docs, ORIGINAL)

        ids = [d.metadata["chunk_id"] for d in out]
        assert ids == ["乙", "甲"]
        assert out[0].metadata["rerank_score"] == pytest.approx(0.9)
        assert "丙" not in ids
        assert "rerank_score" not in docs[2].metadata

    def test_threshold_and_top_k_applied_once_after_aggregation(self):
        """阈值/top_k 只能在 max 聚合后执行一次，不能先按变体局部过滤。"""
        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({
            ORIGINAL: [0.2000, 0.2500, 0.2000],
            DEPT_QUERY: [0.3100, 0.3200, 0.2000],
        })

        out = _compressor(backend, top_k=1, threshold=0.30).compress_documents(
            docs, ORIGINAL,
        )

        assert [d.metadata["chunk_id"] for d in out] == ["乙"]
        assert out[0].metadata["rerank_score"] == pytest.approx(0.32)


class TestScoreValidation:
    def test_invalid_rows_never_participate_in_max(self):
        docs = _docs("甲", "乙", "丙", "丁")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])

        def _dirty(_rows):
            return [
                (0, 0.9),                 # 合法
                (0, 0.1),                 # 重复索引
                (1, float("nan")),        # NaN
                (1, True),                # 布尔
                (2, "0.95"),              # 字符串
                (3, 1.5),                 # 越界分数
                (99, 0.99),               # 越界索引
                (2,),                     # 结构错误
                ("x", 0.5),               # 非法索引类型
                (2, float("inf")),        # Inf
            ]

        backend = _FakeBackend(
            {ORIGINAL: [], DEPT_QUERY: []},
            rows_override={
                ORIGINAL: lambda _r: [(0, 0.4), (1, 0.35), (2, 0.32)],
                DEPT_QUERY: _dirty,
            },
        )
        trace = _rerank_span()

        out = _compressor(backend, top_k=8, threshold=0.3).compress_documents(docs, ORIGINAL)

        # 甲 取 max(0.4, 0.9)；乙/丙 只保留原问题的合法分数，非法项不参与
        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙", "丙"]
        assert [d.metadata["rerank_score"] for d in out] == [pytest.approx(0.9),
                                                             pytest.approx(0.35),
                                                             pytest.approx(0.32)]
        assert all(math.isfinite(d.metadata["rerank_score"]) for d in out)
        status = _span_of(trace).metrics["query_status"]
        dirty = [row for row in status if row["query"].startswith(DEPT_QUERY[:10])][0]
        assert dirty["status"] == "partial"
        assert dirty["invalid"] >= 6

    def test_validate_rerank_scores_rejects_non_list_payload(self):
        from backend.rag.reranker import validate_rerank_scores

        for payload in ("oops", {"index": 0}, 3.14, None):
            with pytest.raises(ValueError):
                validate_rerank_scores(payload, 3)


class TestDegradation:
    def test_variant_failure_keeps_original_scores(self):
        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({ORIGINAL: [0.8, 0.5, 0.4]}, fail={DEPT_QUERY})
        trace = _rerank_span()

        out = _compressor(backend).compress_documents(docs, ORIGINAL)

        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙", "丙"]
        assert out[0].metadata["rerank_status"] == "partial"
        span = _span_of(trace)
        assert span.metrics["scored_query_count"] == 1
        failed = [row for row in span.metrics["query_status"] if row["status"] == "error"]
        assert len(failed) == 1

    def test_original_failure_falls_back_to_passthrough(self):
        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({DEPT_QUERY: [0.9, 0.9, 0.9]}, fail={ORIGINAL})
        trace = _rerank_span()

        out = _compressor(backend, top_k=2).compress_documents(docs, ORIGINAL)

        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙"]
        span = _span_of(trace)
        assert span.metrics["fallback"] == "passthrough"
        assert span.status == "error"

    def test_budget_exhausted_skips_extra_queries_without_new_calls(self):
        from backend.rag.retrieval_budget import start_retrieval_budget

        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY, CERT_QUERY])
        backend = _FakeBackend({ORIGINAL: [0.6, 0.4, 0.35]})
        trace = _rerank_span()
        start_retrieval_budget(50)  # 远低于 RERANK_MQ_MIN_BUDGET_MS

        out = _compressor(backend).compress_documents(docs, ORIGINAL)

        assert len(backend.calls) == 1
        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙", "丙"]
        metrics = _span_of(trace).metrics
        assert metrics["scored_query_count"] == 1
        assert metrics["degrade"] == "budget_insufficient"
        skipped = [row for row in metrics["query_status"] if row["status"] == "skipped_budget"]
        assert len(skipped) == 2

    def test_deadline_is_shared_across_queries(self):
        from backend.rag.retrieval_budget import start_retrieval_budget

        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({
            ORIGINAL: [0.6, 0.4, 0.35], DEPT_QUERY: [0.7, 0.5, 0.4],
        })
        budget = start_retrieval_budget(8000)

        _compressor(backend).compress_documents(docs, ORIGINAL)

        deadlines = {call["deadline_at"] for call in backend.calls}
        assert deadlines == {budget.deadline_at}


class TestSingleQueryPath:
    def test_no_runtime_queries_keeps_legacy_behavior(self):
        docs = _docs("甲", "乙", "丙")
        backend = _FakeBackend({})

        out = _compressor(backend, top_k=2).compress_documents(docs, ORIGINAL)

        assert backend.calls == [{"query": ORIGINAL, "single": True}]
        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙"]

    def test_runtime_queries_must_start_with_original(self):
        docs = _docs("甲", "乙", "丙")
        docs[0].metadata["_runtime_mq_queries"] = [DEPT_QUERY, CERT_QUERY]
        backend = _FakeBackend({})

        _compressor(backend, top_k=8).compress_documents(docs, ORIGINAL)

        assert backend.calls == [{"query": ORIGINAL, "single": True}]

    def test_single_runtime_query_does_not_enable_multi_scoring(self):
        docs = _docs("甲", "乙", "丙")
        docs[0].metadata["_runtime_mq_queries"] = [ORIGINAL]
        backend = _FakeBackend({})

        _compressor(backend, top_k=8).compress_documents(docs, ORIGINAL)

        assert backend.calls == [{"query": ORIGINAL, "single": True}]

    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_small_candidate_sets_skip_scoring(self, count):
        docs = _docs("甲", "乙", "丙")[:count]
        backend = _FakeBackend({ORIGINAL: [0.9] * count})

        out = _compressor(backend).compress_documents(docs, ORIGINAL)

        assert backend.calls == []
        assert len(out) == count

    def test_small_multi_query_set_never_uses_default_one_scores(self):
        docs = _docs("甲", "乙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({ORIGINAL: [0.4], DEPT_QUERY: [0.5]})
        out = _compressor(backend).compress_documents(docs, ORIGINAL)
        assert len(backend.calls) == 2
        assert [doc.metadata["chunk_id"] for doc in out] == ["甲"]
        assert out[0].metadata["rerank_score"] == 0.5

    def test_backend_without_full_scoring_falls_back(self):
        docs = _docs("甲", "乙", "丙")
        _with_queries(docs, [ORIGINAL, DEPT_QUERY])

        class _CompressOnly:
            def __init__(self):
                self.calls = 0

            def compress_documents(self, documents, query, **kwargs):
                self.calls += 1
                return list(documents)[: kwargs.get("top_k", 8)]

        backend = _CompressOnly()
        _compressor(backend).compress_documents(docs, ORIGINAL)
        assert backend.calls == 1


class TestProviderBudgetContract:
    @pytest.mark.parametrize("failure", ["timeout", "connection"])
    def test_transport_failure_is_not_resent_in_same_request(self, monkeypatch, failure):
        import requests

        from backend.rag.context import RagRequestState, clear_context, set_context

        reranker, _ = self._reranker(monkeypatch)
        calls = []

        def fail(*args, **kwargs):
            calls.append(1)
            error = requests.exceptions.Timeout if failure == "timeout" else requests.exceptions.ConnectionError
            raise error("provider unavailable")

        monkeypatch.setattr(reranker, "_rank_once", fail)
        set_context(RagRequestState())
        try:
            for query in ["退款流程", "退款的具体步骤"]:
                with pytest.raises(requests.exceptions.RequestException):
                    reranker.rank(query, ["授权文档"], deadline_at=time.monotonic() + 10)
            assert len(calls) == 1
            # 新用户/新请求不能继承上一请求的失败标记。
            set_context(RagRequestState())
            monkeypatch.setattr(reranker, "_rank_once", lambda *a, **k: [(0, 0.9)])
            assert reranker.rank("退款流程", ["授权文档"], deadline_at=time.monotonic() + 10) == [(0, 0.9)]
        finally:
            clear_context()

    def _reranker(self, monkeypatch, *, budget_ms=None, provider_timeout=4):
        from backend.rag import reranker as mod

        rec = {"timeouts": []}

        class _Resp:
            status_code = 200

            def json(self):
                return {
                    "output": {"results": [
                        {"index": 0, "relevance_score": 0.9},
                        {"index": 1, "relevance_score": 0.4},
                    ]},
                    "usage": {"total_tokens": 12},
                }

        def _post(url, json=None, headers=None, timeout=None):
            rec["timeouts"].append(timeout)
            return _Resp()

        monkeypatch.setattr(mod.requests, "post", _post)
        monkeypatch.setattr(mod.DashScopeReranker, "_record_tokens", lambda *a, **k: None)
        r = mod.DashScopeReranker(
            api_key="sk-test", timeout=provider_timeout,
            model="qwen3.7-text-rerank", api_format="dashscope",
            base_url="https://example.invalid/api/v1",
        )
        return r, rec

    def test_attempt_timeout_never_exceeds_remaining_budget(self, monkeypatch):
        from backend.rag.retrieval_budget import RetrievalBudget, _budget_var

        r, rec = self._reranker(monkeypatch)
        _budget_var.set(RetrievalBudget(total_ms=8000, deadline_at=time.monotonic() + 1.5))

        r.rank("查询", ["a", "b"], top_k=2,
               deadline_at=time.monotonic() + 1.5)

        assert rec["timeouts"] and rec["timeouts"][0] <= 1.5
        assert rec["timeouts"][0] > 0

    def test_no_http_attempt_when_budget_too_small(self, monkeypatch):
        import requests

        from backend.rag.retrieval_budget import RetrievalBudget, _budget_var

        r, rec = self._reranker(monkeypatch)
        _budget_var.set(RetrievalBudget(total_ms=10, deadline_at=time.monotonic() + 0.05))

        with pytest.raises(requests.exceptions.Timeout):
            r.rank("查询", ["a", "b"], top_k=2,
                   deadline_at=time.monotonic() + 0.05)
        assert rec["timeouts"] == []

    def test_single_query_fallback_also_consumes_shared_deadline(self, monkeypatch):
        from backend.rag.retrieval_budget import start_retrieval_budget

        r, rec = self._reranker(monkeypatch)
        start_retrieval_budget(1500)
        r.rank("原问题", ["a", "b"], top_k=2)
        assert 0 < rec["timeouts"][0] <= 1.5

    def test_rank_all_requests_every_candidate(self, monkeypatch):
        from backend.rag import reranker as mod

        r, rec = self._reranker(monkeypatch, provider_timeout=3)
        seen = []

        def _fake_rank(query, documents, top_k=8, *, timeout_s=None, deadline_at=None):
            seen.append({"documents": list(documents), "top_k": top_k})
            return [(i, 0.5) for i in range(len(documents))]

        r.__dict__["rank"] = _fake_rank
        monkeypatch.setattr(mod, "_RERANK_BATCH_SIZE", 9)
        docs = [f"文档{i}" for i in range(20)]

        rows = r.rank_all("查询", docs)

        assert [row[0] for row in rows] == list(range(20))
        assert [call["top_k"] for call in seen] == [9, 9, 2]


def test_aggregate_is_order_independent_of_query_completion():
    """聚合结果只取决于分数，不依赖调用先后/完成先后。"""
    from backend.rag.reranker import aggregate_multi_query_scores

    per_query = {
        ORIGINAL: [(0, 0.5), (1, 0.7)],
        DEPT_QUERY: [(0, 0.8)],
    }
    forward = aggregate_multi_query_scores([ORIGINAL, DEPT_QUERY], per_query)
    backward = aggregate_multi_query_scores(
        [ORIGINAL, DEPT_QUERY],
        {DEPT_QUERY: per_query[DEPT_QUERY], ORIGINAL: per_query[ORIGINAL]},
    )
    assert forward == backward
    assert forward[0][:2] == (0, 0.8)


def test_missing_scores_are_partial_and_diagnostics_are_whitelisted():
    """缺分不得伪装成功，逐查询榜单不泄露正文、路径或私有元数据。"""
    docs = _docs("a", "b", "c")
    docs[0].metadata.update(source="D:/private/a.pdf", api_key="secret-marker")
    docs[0].page_content = "sensitive-body-marker"
    _with_queries(docs, [ORIGINAL, DEPT_QUERY])
    backend = _FakeBackend({ORIGINAL: [0.5, 0.5, 0.4], DEPT_QUERY: [0.8]})
    trace = _rerank_span()
    out = _compressor(backend).compress_documents(docs, ORIGINAL)
    metrics = _span_of(trace).metrics
    assert metrics["rerank_status"] == "partial"
    assert metrics["degrade"] == "query_scoring_incomplete"
    rows = metrics["query_status"]
    assert rows[1]["status"] == "partial" and rows[1]["missing"] == 2
    assert [x["index"] for x in rows[0]["top_candidates"]] == [0, 1, 2]
    assert rows[0]["top_candidates"][0] == {
        "index": 0, "chunk_id": "a", "doc_id": "", "source": "a.pdf", "score": 0.5,
    }
    assert out[0].metadata["rerank_score"] == 0.8
    for forbidden in ("secret-marker", "sensitive-body-marker", "_runtime_mq_queries", "private"):
        assert forbidden not in str(metrics)


@pytest.fixture
def cloud_meter(monkeypatch):
    """真实评分/预算/用量调用链，只替换HTTP、价格源和持久化介质。"""
    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget, pricing
    from backend.observability import llm_usage_store
    from backend.rag.reranker import DashScopeReranker

    class Quota:
        def __init__(self):
            self.entries = {}
            self.lock = threading.Lock()
            self.limit = Decimal(".50")
            self.used = Decimal(0)

        def reserve(self, **kwargs):
            from backend.infra.llm.quota import QuotaExceeded

            with self.lock:
                held = sum(row["amount_cny"] for row in self.entries.values()
                           if row["status"] in ("reserved", "needs_review"))
                if self.used + held + kwargs["amount_cny"] > self.limit:
                    raise QuotaExceeded("free", "day")
                key = str(len(self.entries) + 1)
                self.entries[key] = {**kwargs, "status": "reserved"}
                return SimpleNamespace(reservation_id=key)

        def settle(self, reservation, cost):
            with self.lock:
                row = self.entries[reservation.reservation_id]
                if row["status"] != "settled":
                    self.used += cost
                    row.update(status="settled", cost=cost)

        def release(self, reservation):
            self.entries[reservation.reservation_id]["status"] = "released"

        def mark_needs_review(self, reservation, reason):
            self.entries[reservation.reservation_id].update(
                status="needs_review", reason=reason,
            )
            return True

    quota = Quota()
    events = []
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_cost=".04", max_calls=12), mode="enforce",
        user_id="free-user", tenant_id="free-tenant", quota_store=quota,
    )
    monkeypatch.setitem(budget._states, "concurrent-free", state)
    token = budget._current_request_id.set("concurrent-free")
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *_a: SimpleNamespace(
        require=lambda *_a, **_k: None,
    ))
    monkeypatch.setattr(pricing, "price_usage", lambda *_a, **_k: SimpleNamespace(
        native_cost=Decimal(".001"), native_currency="CNY",
        billed_cost_cny=Decimal(".001"), fx_rate=Decimal(1),
        price_version="free-price", pricing_source="approved_price_table",
        usage_source="provider", input_cost_cny=Decimal(".001"), cost_status="exact",
    ))
    monkeypatch.setattr(llm_usage_store, "get_llm_usage_store", lambda: SimpleNamespace(
        record=lambda event: events.append(event) or True,
    ))
    backend = DashScopeReranker(
        api_key="fake-not-a-secret", timeout=4, model="fake-rerank",
        api_format="dashscope", base_url="https://example.invalid/api/v1",
    )
    with scoped_tool_identity(user_id="free-user", tenant_id="free-tenant"):
        try:
            yield backend, quota, events, state
        finally:
            # 失败断言也必须等Fake迟到调用收口，防止下一例替身接收旧usage。
            from backend.infra import thread_pools

            pool = thread_pools._rerank
            if pool is not None and not pool._shutdown:
                drained = threading.Barrier(3)
                futures = [pool.submit(drained.wait, 5) for _ in range(3)]
                for future in futures:
                    future.result(timeout=6)
            budget._current_request_id.reset(token)


def _response(scores):
    return SimpleNamespace(status_code=200, headers={}, json=lambda: {
        "output": {"results": [{"index": i, "relevance_score": score}
                               for i, score in enumerate(scores)]},
        "usage": {"total_tokens": 12},
    })


def test_cloud_queries_start_together_and_settle_independently(monkeypatch, cloud_meter):
    """串行实现无法越过三方屏障；完成顺序不能改变max聚合及Trace归属。"""
    from backend.infra.llm.budget import current_reservation_id
    from backend.observability.llm_usage_store import current_usage_attribution
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import start_retrieval_budget

    backend, quota, events, state = cloud_meter
    barrier = threading.Barrier(3)
    seen = []

    def post(_url, *, json, headers, timeout):
        seen.append((current_reservation_id(), current_usage_attribution(), timeout))
        barrier.wait(timeout=2)
        query = json["input"]["query"]
        scores = [0.4, 0.8, 0.3] if query == CERT_QUERY else [0.7, 0.4, 0.3]
        return _response(scores)

    monkeypatch.setattr(mod.requests, "post", post)
    trace = _rerank_span()
    budget = start_retrieval_budget(2500)
    docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
    out = _compressor(backend).compress_documents(docs, ORIGINAL)

    assert out[0].metadata["chunk_id"] == "乙"
    assert state.calls == 3 and state.total_tokens == 36
    assert quota.used == Decimal(".003") and len(events) == 3
    assert len({row[0] for row in seen}) == 3
    assert all(row[1]["tenant_id"] == "free-tenant" for row in seen)
    assert all(row[1]["user_id"] == "free-user" for row in seen)
    assert all(row[1]["request_id"] == "concurrent-free" for row in seen)
    assert all(0 < row[2] <= 2.5 for row in seen)
    assert current_reservation_id() == ""
    assert all(row["status"] == "settled" for row in quota.entries.values())
    metrics = _span_of(trace).metrics
    assert metrics["scoring_strategy"] == "bounded_parallel"
    assert metrics["scored_query_count"] == 3 and metrics["query_count"] == 3
    assert [row["query"] for row in metrics["query_status"]] == [ORIGINAL, DEPT_QUERY, CERT_QUERY]
    attempts = [s for s in trace.spans if s.span_id.startswith("rerank_provider_attempt")]
    assert len({s.span_id for s in attempts}) == 3
    assert all(s.parent_id == "rerank" for s in attempts)
    assert len({s.metrics["reservation_id"] for s in attempts}) == 3
    assert {s.metrics["query_index"] for s in attempts} == {0, 1, 2}
    assert all(event["trace_id"] == trace.id for event in events)
    assert budget.remaining_ms() > 0


def test_cloud_late_usage_settles_without_changing_returned_docs(monkeypatch, cloud_meter):
    """故意忽略HTTP超时的Fake：业务先结束，迟到usage仍结算到原调用。"""
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import start_retrieval_budget

    backend, quota, events, state = cloud_meter
    release = threading.Event()
    barrier = threading.Barrier(3)

    def post(*_a, **_k):
        barrier.wait(timeout=2)
        assert release.wait(3)
        return _response([0.4, 0.9, 0.5])

    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod, "_RERANK_MQ_MIN_BUDGET_MS", 1)
    _rerank_span()
    start_retrieval_budget(350)
    docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
    try:
        out = _compressor(backend).compress_documents(docs, ORIGINAL)
        assert [d.metadata["chunk_id"] for d in out] == ["甲", "乙", "丙"]
        assert not any("rerank_score" in d.metadata for d in out)
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while any(row["status"] == "reserved" for row in quota.entries.values()):
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert state.calls == 3 and state.total_tokens == 36
    assert len(events) == 3 and quota.used == Decimal(".003")
    assert not any("rerank_score" in d.metadata for d in out)


def test_no_outbound_when_reservation_consumes_deadline(monkeypatch, cloud_meter):
    """预占前足够、预占后不足时，不发HTTP、不制造未知usage。"""
    from backend.rag import reranker as mod

    backend, quota, events, _state = cloud_meter
    reserve = quota.reserve

    def slow_reserve(**kwargs):
        result = reserve(**kwargs)
        time.sleep(.08)
        return result

    monkeypatch.setattr(quota, "reserve", slow_reserve)
    monkeypatch.setattr(mod.requests, "post", lambda *_a, **_k: pytest.fail("不应外呼"))
    with pytest.raises(mod.requests.exceptions.Timeout):
        backend.rank("q", ["a", "b"], deadline_at=time.monotonic() + .25)
    assert len(events) == 0
    assert all(row["status"] == "released" for row in quota.entries.values())


def test_rc031_timing_replay_scores_all_three_within_shared_budget(monkeypatch, cloud_meter):
    """按实测剩余3.128s/单次1.924s同比缩放，串行三次无法完成。"""
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import start_retrieval_budget

    backend, _quota, _events, _state = cloud_meter
    monkeypatch.setattr(mod, "_RERANK_MQ_MIN_BUDGET_MS", 240)

    def post(*_a, **_k):
        time.sleep(.3848)
        return _response([.6, .5, .4])

    monkeypatch.setattr(mod.requests, "post", post)
    trace = _rerank_span()
    start_retrieval_budget(625.6)
    docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
    _compressor(backend).compress_documents(docs, ORIGINAL)
    assert _span_of(trace).metrics["scored_query_count"] == 3
    assert _span_of(trace).status == "success"


def test_original_failure_preserves_unknown_charge_and_passthrough(monkeypatch, cloud_meter):
    """并发变体成功也不得代替失败的原问题；未知费用继续占额。"""
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import start_retrieval_budget

    backend, quota, events, _state = cloud_meter
    barrier = threading.Barrier(3)

    def post(_url, *, json, **_kwargs):
        barrier.wait(timeout=2)
        if json["input"]["query"] == ORIGINAL:
            raise RuntimeError("fake Provider failure after submission")
        return _response([.1, .99, .9])

    monkeypatch.setattr(mod.requests, "post", post)
    trace = _rerank_span()
    start_retrieval_budget(2500)
    docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
    out = _compressor(backend).compress_documents(docs, ORIGINAL)
    deadline = time.monotonic() + 3
    while any(row["status"] == "reserved" for row in quota.entries.values()):
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert [doc.metadata["chunk_id"] for doc in out] == ["甲", "乙", "丙"]
    assert not any("rerank_score" in doc.metadata for doc in out)
    assert _span_of(trace).metrics["fallback"] == "passthrough"
    assert len(events) == 3
    statuses = [row["status"] for row in quota.entries.values()]
    assert statuses.count("settled") == 2 and statuses.count("needs_review") == 1
    assert quota.used == Decimal(".002")


def test_rerank_pool_is_bounded_and_separates_request_identity(monkeypatch, cloud_meter):
    """两个租户同时评分，最多3个HTTP在途；身份和账本不能串用。"""
    from concurrent.futures import ThreadPoolExecutor

    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget
    from backend.observability.tracer import trace_collector
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import scoped_retrieval_budget

    backend, quota, events, _state = cloud_meter
    counter = {"active": 0, "peak": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def post(*_args, **_kwargs):
        with lock:
            counter["active"] += 1
            counter["peak"] = max(counter["peak"], counter["active"])
        try:
            barrier.wait(timeout=2)
            time.sleep(.03)
            return _response([.6, .5, .4])
        finally:
            with lock:
                counter["active"] -= 1

    monkeypatch.setattr(mod.requests, "post", post)
    for name in ("one", "two"):
        monkeypatch.setitem(budget._states, name, budget.RequestBudget(
            budget.RequestBudgetLimits(max_cost=".04"), mode="enforce",
            user_id=f"user-{name}", tenant_id=f"tenant-{name}", quota_store=quota,
        ))

    def run(name):
        token = budget._current_request_id.set(name)
        try:
            with scoped_tool_identity(user_id=f"user-{name}", tenant_id=f"tenant-{name}"), \
                 scoped_retrieval_budget(2500):
                trace = trace_collector.start("q", session_id=name)
                docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
                _compressor(backend).compress_documents(docs, ORIGINAL)
                assert _span_of(trace).metrics["scored_query_count"] == 3
                return trace.id
        finally:
            budget._current_request_id.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        trace_ids = dict(zip(("one", "two"), pool.map(run, ("one", "two"))))
    assert counter["peak"] == 3
    assert len(events) == 6 and len(quota.entries) == 6
    for event in events:
        name = event["request_id"]
        assert event["user_id"] == f"user-{name}"
        assert event["tenant_id"] == f"tenant-{name}"
        assert event["trace_id"] == trace_ids[name]
        assert event["session_id"] == name


def test_tight_quota_preserves_original_admission(monkeypatch, cloud_meter):
    """仅够一笔预占时，变体不能抢走原问题额度或绕过PG式硬限。"""
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import start_retrieval_budget

    backend, quota, events, state = cloud_meter
    quota.limit = Decimal(".04")
    seen = []

    def post(_url, *, json, **_kwargs):
        seen.append(json["input"]["query"])
        time.sleep(.05)
        return _response([.8, .5, .4])

    monkeypatch.setattr(mod.requests, "post", post)
    trace = _rerank_span()
    start_retrieval_budget(2500)
    docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
    out = _compressor(backend).compress_documents(docs, ORIGINAL)
    assert seen == [ORIGINAL]
    assert state.calls == 1 and len(events) == 1
    assert out[0].metadata["rerank_score"] == .8
    assert _span_of(trace).metrics["scored_query_count"] == 1
    assert quota.used == Decimal(".001")


def test_queued_queries_cancel_without_paid_dispatch(monkeypatch, cloud_meter):
    """专池被占满时截止即返回，排队任务取消后不得再发HTTP。"""
    from concurrent.futures import ThreadPoolExecutor

    from backend.infra import thread_pools
    from backend.rag import reranker as mod
    from backend.rag.retrieval_budget import start_retrieval_budget

    backend, quota, events, _state = cloud_meter
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocker = pool.submit(release.wait, 3)
        monkeypatch.setattr(thread_pools, "_rerank", pool)
        monkeypatch.setattr(mod.requests, "post", lambda *_a, **_k: pytest.fail("排队后不应外呼"))
        _rerank_span()
        start_retrieval_budget(80)
        docs = _with_queries(_docs("甲", "乙", "丙"), [ORIGINAL, DEPT_QUERY, CERT_QUERY])
        try:
            out = _compressor(backend).compress_documents(docs, ORIGINAL)
            assert len(out) == 3
        finally:
            release.set()
        blocker.result(timeout=1)
    assert events == [] and quota.entries == {}
