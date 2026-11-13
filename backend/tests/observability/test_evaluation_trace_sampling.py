"""RAG 评测结果逐题 Trace 链接必须绕过普通成功 Trace 采样。"""


def test_rag_evaluation_trace_is_kept_when_sampling_is_disabled(monkeypatch):
    from backend.observability.trace_writer import _should_keep

    monkeypatch.setattr(
        "backend.observability.trace_writer.TRACE_SAMPLING_RATE",
        0.0,
    )

    assert _should_keep({"workflow_name": "rag_eval", "status": "success"})
    assert not _should_keep({"workflow_name": "rag_query", "status": "success"})
