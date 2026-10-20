import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor

from backend.observability.tracer import TraceCollector


def test_background_trace_does_not_inherit_another_thread_request():
    collector = TraceCollector()
    request_trace = collector.start("request")

    def run_background_workflow():
        workflow_trace = collector.start("scheduled workflow")
        parent_id = workflow_trace.parent_id
        collector.bind(None)
        return parent_id

    with ThreadPoolExecutor(max_workers=1) as pool:
        background_parent_id = pool.submit(run_background_workflow).result()

    assert background_parent_id is None
    assert request_trace.children_ids == []
    assert collector.current() is request_trace


def test_async_task_without_context_does_not_use_thread_fallback():
    collector = TraceCollector()
    request_trace = collector.start("sync request")

    async def check_isolated_task():
        collector.bind(None)

        async def run_background_workflow():
            workflow_trace = collector.start("scheduled workflow")
            return workflow_trace.parent_id

        task = contextvars.Context().run(
            asyncio.create_task, run_background_workflow()
        )
        return await task

    background_parent_id = asyncio.run(check_isolated_task())

    assert background_parent_id is None
    assert request_trace.children_ids == []
    collector.bind(None)


def test_span_completion_in_worker_keeps_explicit_trace_owner():
    collector = TraceCollector()
    request_trace = collector.start("request")
    span = collector.start_span("request_span")
    callbacks = []
    collector.subscribe(lambda trace, ended_span: callbacks.append((trace, ended_span)))

    def finish_without_inherited_context():
        collector.bind(None)
        collector.end_span(span)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(finish_without_inherited_context).result()

    assert callbacks == [(request_trace, span)]


def test_event_loop_callback_without_context_avoids_thread_fallback():
    collector = TraceCollector()
    request_trace = collector.start("sync request")

    async def run_callback():
        collector.bind(None)
        loop = asyncio.get_running_loop()
        result = loop.create_future()

        def start_scheduled_workflow():
            workflow_trace = collector.start("scheduled workflow")
            result.set_result(workflow_trace.parent_id)

        loop.call_soon(start_scheduled_workflow)
        return await result

    background_parent_id = asyncio.run(run_callback())

    assert background_parent_id is None
    assert request_trace.children_ids == []
    collector.bind(None)
