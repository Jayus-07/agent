"""LLM 用量记录的处理运行归属测试。"""

from backend.observability.llm_usage_store import current_usage_attribution
from backend.shared.processing_context import ProcessingBinding, bind_processing


def test_usage_attribution_includes_processing_binding():
    binding = ProcessingBinding(
        run_id="run-1", step_id="step-1", role="metadata_extract", stage="metadata_extract"
    )

    with bind_processing(binding):
        attribution = current_usage_attribution()

    assert attribution["run_id"] == "run-1"
    assert attribution["step_id"] == "step-1"
    assert attribution["role"] == "metadata_extract"
    assert attribution["stage"] == "metadata_extract"
