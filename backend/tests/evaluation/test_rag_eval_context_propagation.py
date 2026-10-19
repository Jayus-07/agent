"""RAG 评测线程池必须保留租户、用户、Trace 与预算上下文。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def test_context_submission_keeps_isolated_eval_attribution(monkeypatch):
    from backend.core.request_context import scoped_tool_identity
    from backend.evaluation.runners.rag import _submit_with_context
    from backend.infra.llm import budget as budget_mod
    from backend.infra.llm.proxy import (
        _thread_local_user_id,
        scoped_current_user_id,
    )
    from backend.observability.llm_usage_store import current_usage_attribution
    from backend.observability.tracer import trace_collector

    trace_collector.clear_for_test()
    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: object(),
    )

    def read_context():
        attribution = current_usage_attribution()
        budget = budget_mod.current_request_budget()
        return {
            "tenant_id": attribution["tenant_id"],
            "user_id": attribution["user_id"],
            "trace_id": attribution["trace_id"],
            "request_id": budget_mod._current_request_id.get(),
            "proxy_user_id": _thread_local_user_id(),
            "budget_user_id": budget.user_id if budget else "",
            "budget_tenant_id": budget.tenant_id if budget else "",
        }

    expected = {}
    futures = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            for suffix in ("a", "b"):
                user_id = f"eval-user-{suffix}"
                tenant_id = f"eval-tenant-{suffix}"
                request_id = f"eval:context-{suffix}"
                with scoped_current_user_id(user_id):
                    with scoped_tool_identity(
                        user_id=user_id, tenant_id=tenant_id,
                    ):
                        trace = trace_collector.start(
                            question=f"eval context {suffix}",
                            session_id=f"eval-{suffix}",
                        )
                        with budget_mod.scoped_request_budget(
                            request_id,
                            user_id=user_id,
                            tenant_id=tenant_id,
                        ):
                            futures[suffix] = _submit_with_context(
                                executor, read_context,
                            )
                expected[suffix] = {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "trace_id": trace.id,
                    "request_id": request_id,
                    "proxy_user_id": user_id,
                    "budget_user_id": user_id,
                    "budget_tenant_id": tenant_id,
                }

            actual = {key: future.result(timeout=5) for key, future in futures.items()}
        finally:
            trace_collector.clear_for_test()

    assert actual == expected


def test_rag_case_and_ragas_submissions_use_context_copy():
    import inspect

    import backend.evaluation.runners.rag as rag_runner

    source = inspect.getsource(rag_runner._run_rag)
    assert "_submit_with_context(executor, lambda: _eval_case(case))" in source
    assert "_submit_with_context(_pool, _guarded_case, c)" in source
    assert "_submit_with_context(_ragas_pool, _run_ragas, j)" in source
