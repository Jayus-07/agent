"""处理运行/阶段领域契约测试。"""

import pytest

from backend.rag.indexing.processing_lineage import (
    ModelIdentity,
    ProcessingRunContext,
)


def test_stage_snapshot_keeps_actual_model_identity_and_usage():
    context = ProcessingRunContext.create(
        doc_id="doc-1",
        file_hash="sha256:file",
        operation="upload",
        config_snapshot={"metadata_extract": {"model": "qwen-test"}},
    )

    step_id = context.begin_stage(
        "metadata_extract",
        role="metadata_extract",
        engine_type="llm",
        ordinal=3,
        model=ModelIdentity(
            role="metadata_extract",
            engine_type="llm",
            provider="qwen",
            model_name="qwen-test",
            model_revision="deploy-7",
            config_source="code-default",
        ),
    )
    context.finish_stage(
        step_id,
        status="success",
        input_count=1,
        output_count=1,
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    )

    snapshot = context.snapshot()
    assert snapshot.run_id
    assert snapshot.steps[0].step_id == step_id
    assert snapshot.steps[0].model_name == "qwen-test"
    assert snapshot.steps[0].provider == "qwen"
    assert snapshot.steps[0].total_tokens == 120
    assert snapshot.steps[0].status == "success"


def test_skipped_stage_cannot_claim_a_model():
    context = ProcessingRunContext.create("doc-1", "hash-1", "upload")

    with pytest.raises(ValueError, match="skipped 阶段不能填写实际模型"):
        context.begin_stage(
            "ocr",
            role="ocr",
            engine_type="ocr",
            ordinal=1,
            model=ModelIdentity(
                role="ocr",
                engine_type="ocr",
                provider="rapidocr",
                model_name="rapidocr",
            ),
            skip_reason="text_layer_sufficient",
        )


def test_run_and_stage_can_only_finish_once():
    context = ProcessingRunContext.create("doc-1", "hash-1", "upload")
    step_id = context.begin_stage(
        "parser", role=None, engine_type="parser", ordinal=0
    )
    context.finish_stage(step_id, status="success")

    with pytest.raises(RuntimeError, match="阶段已完成"):
        context.finish_stage(step_id, status="success")

    context.finish("success")
    with pytest.raises(RuntimeError, match="运行已完成"):
        context.finish("success")


def test_negative_usage_is_rejected():
    context = ProcessingRunContext.create("doc-1", "hash-1", "upload")
    step_id = context.begin_stage(
        "embedding", role="embedding", engine_type="embedding", ordinal=7
    )

    with pytest.raises(ValueError, match="Token 不能为负数"):
        context.finish_stage(
            step_id,
            status="success",
            usage={"prompt_tokens": -1, "completion_tokens": 0},
        )


def test_stage_model_can_be_filled_after_route_decision():
    context = ProcessingRunContext.create("doc-1", "hash-1", "index")
    step_id = context.begin_stage(
        "metadata_extract",
        role="metadata_extract",
        engine_type="llm",
    )

    context.set_stage_model(
        step_id,
        ModelIdentity(
            role="metadata_extract",
            engine_type="llm",
            provider="deepseek",
            model_name="deepseek-chat",
        ),
    )

    step = context.snapshot().steps[0]
    assert step.provider == "deepseek"
    assert step.model_name == "deepseek-chat"


def test_stage_can_be_marked_skipped_after_runtime_probe():
    context = ProcessingRunContext.create("doc-1", "hash-1", "index")
    step_id = context.begin_stage("ocr", role="ocr", engine_type="ocr")

    context.finish_stage(
        step_id,
        status="skipped",
        skip_reason="text_layer_sufficient",
    )

    step = context.snapshot().steps[0]
    assert step.status == "skipped"
    assert step.skip_reason == "text_layer_sufficient"
