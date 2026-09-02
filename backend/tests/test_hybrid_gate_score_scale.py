"""hybrid.py Evidence Gate 注入的量纲修复测试（P0-4）。

背景：hybrid_retrieve 在 rerank 之前注入 gate decision，此时文档只有
rrf_score（RRF 量纲，上限 ≈0.033），却与 VEC_MIN_SCORE=0.2（余弦相似度
量纲）比较 → 非空召回必误判 LOW_RELEVANCE。
修复：仅当文档携带相似度语义分数（rerank_score/similarity）时才做分数
阈值检查；只有 RRF 分数时跳过阈值（交由下游带真实分数的 Gate 判定）。
"""
import pytest


@pytest.fixture(autouse=True)
def _enable_gate(monkeypatch):
    monkeypatch.setenv("EVIDENCE_GATE_ENABLED", "true")
    yield


class _StubDecision:
    def __init__(self, passed, reason=None, diagnostics=None):
        self.passed = passed
        self.reason = reason
        self.diagnostics = diagnostics or {}

    def to_metrics(self):
        return {"gate_passed": self.passed}


def _make_docs(score_field, score_value, n=3):
    from langchain_core.documents import Document

    return [
        Document(
            page_content=f"内容{i}",
            metadata={"chunk_id": f"c{i}", "rrf_score": 0.016,
                      **({score_field: score_value} if score_field else {})},
        )
        for i in range(n)
    ]


class TestHybridGateScoreScale:
    def _run_gate(self, monkeypatch, docs):
        """复现 hybrid.py 注入 gate 的调用，返回传给 evidence_gate_retrieval 的 kwargs。"""
        captured = {}

        def fake_gate(d, query_analysis=None, **kwargs):
            captured.update(kwargs)
            return _StubDecision(passed=True)

        import backend.rag.evidence_gate.operations as ops
        from backend.rag.retrieval import hybrid

        monkeypatch.setattr(hybrid, "QueryAnalyzer", lambda: None, raising=False)

        # 直接调用被测辅助函数（若不存在则视为未实现 → 测试失败）
        decision = hybrid._evaluate_retrieval_gate(docs, "退款审核时间是多少？")
        return decision, captured

    def test_rrf_only_docs_skip_score_threshold(self, monkeypatch):
        """只有 rrf_score（0.016）时，阈值检查必须跳过 → 不因量纲误拒。"""
        from backend.rag.retrieval import hybrid

        docs = _make_docs(None, None)
        decision = hybrid._evaluate_retrieval_gate(docs, "退款审核时间是多少？")

        # 不应以 LOW_RELEVANCE 拒答（0.016 < 0.2 的量纲陷阱）
        assert decision.passed is True

    def test_similarity_docs_still_checked(self, monkeypatch):
        """携带 similarity 分数时，仍按阈值正常判定（低分应拒）。"""
        from backend.rag.retrieval import hybrid

        docs = _make_docs("similarity", 0.05)
        decision = hybrid._evaluate_retrieval_gate(docs, "退款审核时间是多少？")

        assert decision.passed is False

    def test_similarity_docs_pass_above_threshold(self, monkeypatch):
        """携带 similarity 分数且高于阈值 → 放行。"""
        from backend.rag.retrieval import hybrid

        docs = _make_docs("similarity", 0.55)
        decision = hybrid._evaluate_retrieval_gate(docs, "退款审核时间是多少？")

        assert decision.passed is True
