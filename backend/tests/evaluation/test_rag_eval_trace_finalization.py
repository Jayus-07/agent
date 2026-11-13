"""RAG 评测必须保存可查询的 Trace，并标记运行错误。"""
from __future__ import annotations

import inspect
from types import SimpleNamespace


def test_finish_eval_trace_persists_success_and_runtime_error(monkeypatch):
    from backend.evaluation.runners import rag as rag_runner
    from backend.observability.tracer import trace_collector

    started = []
    ended = []
    finished = []
    error_span = object()
    monkeypatch.setattr(
        trace_collector,
        "start_span",
        lambda *args, **kwargs: started.append((args, kwargs)) or error_span,
    )
    monkeypatch.setattr(
        trace_collector,
        "end_span",
        lambda span, **kwargs: ended.append((span, kwargs)),
    )
    monkeypatch.setattr(
        trace_collector,
        "finish",
        lambda trace, **kwargs: finished.append((trace, kwargs)),
    )

    successful_trace = SimpleNamespace(id="success-trace")
    rag_runner._finish_eval_trace(
        successful_trace,
        answer="已核验答案",
        total_ms=42,
    )

    failed_trace = SimpleNamespace(id="failed-trace")
    rag_runner._finish_eval_trace(
        failed_trace,
        answer="",
        total_ms=17,
        error_stage="retrieval",
    )

    assert len(started) == 1
    assert started[0][0] == ("rag_eval_error",)
    assert started[0][1]["input"] == {"error_stage": "retrieval"}
    assert ended == [(error_span, {
        "output": {"error_stage": "retrieval"},
        "status": "error",
    })]
    assert finished == [
        (successful_trace, {
            "answer": "已核验答案",
            "total_ms": 42,
            "model": "",
            "provider": "",
        }),
        (failed_trace, {
            "answer": "",
            "total_ms": 17,
            "model": "",
            "provider": "",
        }),
    ]


def test_finish_eval_trace_queues_completed_records_without_shared_store(monkeypatch):
    from backend.evaluation.runners import rag as rag_runner
    from backend.observability import trace_writer
    from backend.observability.tracer import SpanKind, trace_collector
    from backend.prompts import service as prompt_service

    queued = []
    monkeypatch.setattr(
        trace_collector,
        "_backfill_usage_from_store",
        lambda _record: None,
    )
    monkeypatch.setattr(
        trace_collector,
        "_record_prometheus",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        trace_writer,
        "get_trace_write_queue",
        lambda: SimpleNamespace(enqueue=queued.append),
    )
    monkeypatch.setattr(prompt_service, "collect_prompt_usage", lambda: {})

    trace_collector.clear_for_test()
    try:
        successful_trace = trace_collector.start(
            question="核验问题",
            session_id="eval-success",
            workflow_name="rag_eval",
        )
        root_span = trace_collector.start_span(
            "rag_eval_root",
            name="RAG 评测",
            type="workflow",
            kind=SpanKind.RETRIEVAL.value,
        )
        trace_collector.end_span(root_span)
        rag_runner._finish_eval_trace(
            successful_trace,
            answer="引用证据后的回答",
            total_ms=50,
        )

        failed_trace = trace_collector.start(
            question="异常问题",
            session_id="eval-failed",
            workflow_name="rag_eval",
        )
        rag_runner._finish_eval_trace(
            failed_trace,
            answer="",
            total_ms=12,
            error_stage="retrieval",
        )

        assert queued == [successful_trace, failed_trace]
        assert successful_trace.status == "success"
        assert successful_trace.answer_preview == "引用证据后的回答"
        assert failed_trace.status == "error"
        assert failed_trace.spans[0].status == "error"
    finally:
        trace_collector.clear_for_test()


def test_case_checkpoint_saves_local_non_error_result(monkeypatch, tmp_path):
    from backend.evaluation.models import EvalResult
    from backend.evaluation.runners import rag as rag_runner

    saved = []
    monkeypatch.setattr(
        rag_runner,
        "_append_result_checkpoint",
        lambda path, result: saved.append((path, result.case_id)),
    )

    failed_quality = EvalResult(
        case_id="quality-fail",
        module="rag",
        status="fail",
        expected={},
        actual={"generated_answer": "已完成回答"},
    )
    runtime_error = EvalResult(
        case_id="runtime-error",
        module="rag",
        status="error",
        expected={},
        actual={},
    )

    rag_runner._checkpoint_case_result(tmp_path / "checkpoint.jsonl", [failed_quality])
    rag_runner._checkpoint_case_result(tmp_path / "checkpoint.jsonl", [runtime_error])
    rag_runner._checkpoint_case_result(tmp_path / "checkpoint.jsonl", [])

    assert saved == [(tmp_path / "checkpoint.jsonl", "quality-fail")]


def test_rag_case_checkpoint_reads_case_local_result_holder():
    from backend.evaluation.runners import rag as rag_runner

    source = inspect.getsource(rag_runner._run_rag)

    assert "_checkpoint_case_result(checkpoint_file, result_holder)" in source
    assert "_append_result_checkpoint(checkpoint_file, results[-1])" not in source


def test_rag_runner_finalizes_trace_after_each_case_path():
    from backend.evaluation.runners import rag as rag_runner

    source = inspect.getsource(rag_runner._run_rag)

    assert "trace_error_stage = _stage" in source
    assert "_finish_eval_trace(" in source
    assert "error_stage=trace_error_stage" in source


def test_eval_processing_span_marks_quality_work_and_runtime_errors():
    from backend.evaluation.runners import rag as rag_runner

    span = object()
    started = []
    ended = []

    class Collector:
        def start_span(self, *args, **kwargs):
            started.append((args, kwargs))
            return span

        def end_span(self, actual_span, **kwargs):
            ended.append((actual_span, kwargs))

    collector = Collector()
    actual_span = rag_runner._start_eval_processing_span(collector, "case-1")
    rag_runner._finish_eval_processing_span(collector, actual_span)
    rag_runner._finish_eval_processing_span(
        collector, actual_span, error_stage="generation",
    )

    assert started == [(("rag_eval_processing",), {
        "name": "答案生成与评测判分",
        "type": "workflow",
        "kind": "evaluation",
        "input": {"case_id": "case-1"},
    })]
    assert ended == [
        (span, {"output": {}, "status": "success"}),
        (span, {
            "output": {"error_stage": "generation"},
            "status": "error",
        }),
    ]


def test_case_trace_summary_is_refreshed_after_finalization():
    from backend.evaluation.runners import rag as rag_runner

    spans = [
        SimpleNamespace(
            span_id=span_id,
            parent_id="root",
            name=span_id,
            type="workflow",
            kind="evaluation",
            status="success",
            duration_ms=12,
            sequence=index,
            metrics={},
            input={"private": "question"},
            output={"private": "answer"},
            events=[{"name": "event"}],
            errors=[],
        )
        for index, span_id in enumerate(("root", "evaluation"))
    ]
    trace = SimpleNamespace(id="trace-final", spans=spans)
    result = SimpleNamespace(actual={
        "trace": {
            "trace_id": "trace-final",
            "total_spans": 1,
            "total_trace_ms": 12,
            "prompt_tokens": 7,
            "spans": [],
        },
    })

    rag_runner._sync_eval_trace_result(trace, [result], full_trace=False)

    summary = result.actual["trace"]
    assert summary["trace_id"] == "trace-final"
    assert summary["total_spans"] == 2
    assert summary["total_trace_ms"] == 24
    assert summary["prompt_tokens"] == 7
    assert [span["span_id"] for span in summary["spans"]] == ["root", "evaluation"]
    assert all("input" not in span and "output" not in span for span in summary["spans"])

    rag_runner._sync_eval_trace_result(trace, [result], full_trace=True)
    full_summary = result.actual["trace"]
    assert full_summary["spans"][0]["input"] == {"private": "question"}
    assert full_summary["spans"][0]["output"] == {"private": "answer"}


def test_rag_runner_syncs_result_trace_after_trace_finalization():
    from backend.evaluation.runners import rag as rag_runner

    source = inspect.getsource(rag_runner._run_rag)
    finish_index = source.index("_finish_eval_trace(")
    sync_index = source.index("_sync_eval_trace_result(")
    checkpoint_index = source.index("_checkpoint_case_result(checkpoint_file, result_holder)")
    assert finish_index < sync_index < checkpoint_index
