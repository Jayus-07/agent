"""评估 runner 必须显式使用 evaluation runtime。"""

from __future__ import annotations


def test_eval_runner_constructs_evaluation_pipeline(monkeypatch):
    from backend.evaluation.runners import _common
    from backend.rag import pipeline as pipeline_module

    seen: list[str] = []

    class FakePipeline:
        def __init__(self, *, mode):
            seen.append(mode)

    monkeypatch.setattr(pipeline_module, "RAGPipeline", FakePipeline)
    monkeypatch.setattr(_common, "_rag_pipeline", None)
    monkeypatch.setattr(_common, "_rag_pipeline_error", None)

    result = _common.init_rag_pipeline()

    assert isinstance(result, FakePipeline)
    assert seen == ["evaluation"]
