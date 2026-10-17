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
import time

import pytest
from langchain_core.documents import Document


@pytest.fixture(autouse=True)
def _clean_env():
    from backend.rag.retrieval_budget import clear_retrieval_budget
    from backend.observability.tracer import trace_collector

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
        if docs:
            _with_queries(docs, [ORIGINAL, DEPT_QUERY])
        backend = _FakeBackend({ORIGINAL: [0.9] * count})

        out = _compressor(backend).compress_documents(docs, ORIGINAL)

        assert backend.calls == []
        assert len(out) == count

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
