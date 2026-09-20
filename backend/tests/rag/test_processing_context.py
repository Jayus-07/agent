"""处理血缘上下文的并发隔离测试。"""

from concurrent.futures import ThreadPoolExecutor

from backend.shared.processing_context import (
    ProcessingBinding,
    bind_processing,
    get_processing_binding,
)


def test_binding_nested_context_restores_previous_value():
    outer = ProcessingBinding(
        run_id="run-outer", step_id="step-outer", role="embedding", stage="embedding"
    )
    inner = ProcessingBinding(
        run_id="run-inner", step_id="step-inner", role="ocr", stage="ocr"
    )

    assert get_processing_binding() is None
    with bind_processing(outer):
        assert get_processing_binding() == outer
        with bind_processing(inner):
            assert get_processing_binding() == inner
        assert get_processing_binding() == outer
    assert get_processing_binding() is None


def test_bindings_are_isolated_between_worker_threads():
    def worker(run_id: str, role: str) -> ProcessingBinding | None:
        binding = ProcessingBinding(
            run_id=run_id, step_id=f"step-{run_id}", role=role, stage="llm"
        )
        with bind_processing(binding):
            return get_processing_binding()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker, "run-a", "metadata_extract")
        second = executor.submit(worker, "run-b", "question_gen")

    assert first.result() == ProcessingBinding(
        run_id="run-a", step_id="step-run-a", role="metadata_extract", stage="llm"
    )
    assert second.result() == ProcessingBinding(
        run_id="run-b", step_id="step-run-b", role="question_gen", stage="llm"
    )
    assert get_processing_binding() is None
