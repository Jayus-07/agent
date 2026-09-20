"""元数据内部模型角色与处理阶段的血缘测试。"""

import asyncio
from types import SimpleNamespace

from backend.rag.indexing.processing_lineage import ProcessingRunContext
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder
from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.shared.processing_context import get_processing_binding


def test_embedding_summary_reads_tracked_runtime_model_name():
    """元数据摘要应读取包装器的实际模型，而不是旧 env 默认值。"""
    embedding = SimpleNamespace(_model_name="qwen3.7-text-embedding")

    assert MetadataStage._embedding_model_name(embedding) == (
        "qwen3.7-text-embedding"
    )


class _Repository:
    def __init__(self):
        self.steps = []

    def upsert_stage(self, snapshot):
        self.steps.append(snapshot)

    def create_run(self, snapshot):
        return snapshot.run_id

    def finish_run(self, *args, **kwargs):
        return None


def test_question_generation_has_its_own_role_and_step(monkeypatch):
    binding_seen = []

    def _fake_generate(chunks, doc_type):
        binding_seen.append(get_processing_binding())
        return [[f"如何处理{doc_type}"] for _ in chunks], {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "model": "question-model-2026",
        }

    from backend.rag.preprocessing import question_gen
    monkeypatch.setattr(question_gen, "generate_chunk_questions", _fake_generate)

    repository = _Repository()
    context = ProcessingRunContext.create("doc-1", "hash-1", "upload")
    recorder = ProcessingRunRecorder(context, repository)
    stage = MetadataStage(registry=None, embedding=None, department="qa")

    questions, usage = asyncio.run(
        stage._generate_questions_with_lineage(
            ["正文一", "正文二"], "policy", recorder,
        )
    )

    assert len(questions) == 2
    assert usage["prompt_tokens"] == 3
    assert binding_seen[0] is not None
    assert binding_seen[0].role == "question_gen"
    assert repository.steps[0].stage == "question_gen"
    assert repository.steps[0].role == "question_gen"
    assert repository.steps[0].model_name == "question-model-2026"
    assert repository.steps[0].total_tokens == 5
