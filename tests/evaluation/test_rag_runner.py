"""测试 _run_rag 的 KB 软 fallback + trace 包裹逻辑。

Why:
  防止 KB 软 fallback 和 trace 包裹代码回归，
  这两个是评测链路打通的关键。
"""
import pytest
from unittest.mock import MagicMock, patch

from backend.evaluation.models import TestCase, EvalResult
from backend.evaluation.runners.builtin import _run_rag


def _make_pipeline_with_doc_db(doc_db_metadata: list[dict]):
    """构造一个 mock pipeline，含 doc_db.get 返回指定 metadata。"""
    pipeline = MagicMock()
    pipeline.doc_db.get.return_value = {"metadatas": doc_db_metadata}
    pipeline.doc_db.similarity_search.return_value = []
    return pipeline


def test_missing_eval_kb_is_reported_without_default_fallback():
    """评测 KB 不在 doc_db 时报告错误，不静默回退 default。"""
    pipeline = _make_pipeline_with_doc_db(
        [{"kb_id": "policy_general"}, {"kb_id": "policy_general"}]
    )

    # 统一评测 scope 合法，但数据库中没有该 KB；当前契约明确拒绝回退。
    case = TestCase(
        id="R001",
        question="Amazon Listing 标题的格式是什么？",
        module="rag",
        expected={"relevant_docs": ["KD0001"]},
        metadata={"kb_id": "rag_eval_kb", "fixture_set": "baseline"},
    )

    with patch("backend.evaluation.runners.rag.init_rag_pipeline", return_value=pipeline):
        with patch("backend.evaluation.runners.rag.get_full_retriever") as mock_get_ret:
            mock_get_ret.return_value.invoke.return_value = []
            results = _run_rag([case])

    assert len(results) == 1
    assert results[0].status == "error"
    assert "拒绝回退" in (results[0].error_msg or "")


def test_default_kb_requires_explicit_eval_scope():
    """default KB 不得绕过统一评测 scope 校验。"""
    case = TestCase(
        id="R002",
        question="退款政策",
        module="rag",
        expected={"relevant_docs": []},
        metadata={"kb_id": "default", "fixture_set": "baseline"},
    )

    with pytest.raises(ValueError, match="统一 KB rag_eval_kb"):
        _run_rag([case])


def test_kb_probe_handles_doc_db_exception():
    """doc_db.get 抛异常时应该优雅降级，不让评测整体崩溃。"""
    pipeline = MagicMock()
    pipeline.doc_db.get.side_effect = Exception("ChromaDB 挂了")

    case = TestCase(
        id="R003",
        question="测试",
        module="rag",
        expected={},
        metadata={"kb_id": "rag_eval_kb", "fixture_set": "baseline"},
    )

    pipeline.doc_db.similarity_search.return_value = []
    with patch("backend.evaluation.runners.rag.init_rag_pipeline", return_value=pipeline):
        with patch("backend.evaluation.runners.rag.get_full_retriever") as mock_get_ret:
            mock_get_ret.return_value.invoke.return_value = []
            # 不应该抛异常
            results = _run_rag([case])

    assert len(results) == 1


def test_pipeline_unavailable_returns_error_results():
    """pipeline 初始化失败时，每个 case 都应该返回 error 状态。"""
    cases = [
        TestCase(
            id="R001", question="q1", module="rag", expected={},
            metadata={"kb_id": "rag_eval_kb", "fixture_set": "baseline"},
        ),
        TestCase(
            id="R002", question="q2", module="rag", expected={},
            metadata={"kb_id": "rag_eval_kb", "fixture_set": "baseline"},
        ),
    ]

    with patch("backend.evaluation.runners.rag.init_rag_pipeline", return_value=None):
        results = _run_rag(cases)

    assert len(results) == 2
    assert all(r.status == "error" for r in results)
    assert all("RAG pipeline not available" in (r.error_msg or "") for r in results)
