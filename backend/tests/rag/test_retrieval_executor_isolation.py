"""检索线程池拓扑回归测试（Phase 3 / P0 Executor Starvation）。

背景（施工前审计实锤）：MultiQuery 把变体父任务提交进 retr-inner(6)，
每个变体的子 leg（hybrid vector/BM25）又提交回同一个池并阻塞等待——
父占坑等子、子排在父后，2 个并发 MQ 请求 × 3 变体 = 6 父占满 inner，
子任务永不调度 = 线程饥饿死锁。

本文件锁死三条铁律：
1. multi_query 专池与 inner/outer 物理隔离；
2. 父任务等待提交到 inner 的子任务时，inner 被占满也不死锁
   （旧共享池拓扑下本测试会以 TimeoutError 失败）；
3. 慢 Provider 改写任务不能占住 outer 检索线程。

测试全部使用真实 ThreadPoolExecutor 与真实并发——禁止 mock 池后
声称性能修好（spec §38）。
"""
from __future__ import annotations

import threading
import time

from langchain_core.documents import Document

from backend.infra.thread_pools import (
    retrieval_pool_inner,
    retrieval_pool_multi_query,
    retrieval_pool_multi_query_rewrite,
    retrieval_pool_outer,
    submit_rag_task,
)


def test_multi_query_and_rewrite_pools_are_physically_distinct():
    """检索池和可能阻塞的 Provider 改写池必须相互隔离。"""
    mq = retrieval_pool_multi_query()
    rewrite = retrieval_pool_multi_query_rewrite()
    inner = retrieval_pool_inner()
    outer = retrieval_pool_outer()
    assert mq is not inner
    assert mq is not outer
    assert rewrite is not mq
    assert rewrite is not inner
    assert rewrite is not outer
    assert inner is not outer


def test_slow_rewrite_provider_cannot_starve_outer_retrieval():
    """不可物理取消的改写调用不能占用检索 outer worker。"""
    rewrite_pool = retrieval_pool_multi_query_rewrite()
    started = threading.Event()
    release = threading.Event()

    def _slow_provider() -> None:
        started.set()
        release.wait(10)

    rewrite = rewrite_pool.submit(_slow_provider)
    try:
        assert started.wait(5)
        retrieval = submit_rag_task("outer", lambda: "retrieval-ready")
        assert retrieval.result(timeout=2) == "retrieval-ready"
    finally:
        release.set()
        rewrite.result(timeout=5)


def test_parent_waiting_inner_child_no_deadlock_when_inner_saturated():
    """inner 池被占满时，MQ 父任务等 inner 子任务仍能在超时内完成。

    旧同池拓扑下：6 个父任务排进 inner 队列、占满全部 worker 后各自
    等待排在自己身后的子任务 → 死锁（子任务永不调度）。
    新拓扑：父任务在 multi_query 池运行，子任务在 inner 排队，
    barrier 释放后全部完成。
    """
    release = threading.Event()
    inner = retrieval_pool_inner()

    def _blocker() -> None:
        release.wait(10)

    # 占满 inner 全部 worker（含已有并发余量，多提交几个阻塞任务）
    saturate_count = inner._max_workers + 2
    blockers = [inner.submit(_blocker) for _ in range(saturate_count)]
    try:
        # 等 blocker 真正占住 worker（队列清空 = 全部在执行）
        deadline = time.monotonic() + 5
        while inner._work_queue.qsize() > 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        def _make_parent(i: int):
            def _parent() -> int:
                # 模拟 hybrid 变体的子 leg：提交到 inner 并阻塞等待
                child = submit_rag_task("inner", lambda: i)
                return child.result(timeout=10)

            return _parent

        parents = [
            submit_rag_task("multi_query", _make_parent(i)) for i in range(6)
        ]
        time.sleep(0.05)  # 父任务全部进入「等子任务」状态
        release.set()

        results = [f.result(timeout=15) for f in parents]
        assert sorted(results) == list(range(6))
    finally:
        release.set()
        for b in blockers:
            b.result(timeout=10)


def test_concurrent_multi_query_requests_no_starvation():
    """spec §32 语义：两个并发复杂 MQ 请求（各 3 变体，父等 inner 子）
    同时运行不得因 executor starvation 卡死。"""
    release = threading.Event()

    def _request(tag: str):
        def _run() -> list[int]:
            outs = []
            for v in range(3):  # 每请求 3 个变体，变体内等 inner 子任务
                child = submit_rag_task(
                    "inner", lambda t=tag, v=v: f"{t}-{v}",
                )
                outs.append(child.result(timeout=10))
            return outs

        return _run

    try:
        f1 = submit_rag_task("multi_query", _request("req1"))
        f2 = submit_rag_task("multi_query", _request("req2"))
        time.sleep(0.05)
        release.set()
        r1, r2 = f1.result(timeout=15), f2.result(timeout=15)
        assert len(r1) == 3 and len(r2) == 3
        assert r1 == [f"req1-{i}" for i in range(3)]
        assert r2 == [f"req2-{i}" for i in range(3)]
    finally:
        release.set()


def test_submit_rag_task_records_pool_metrics():
    """spec §20：每次提交必须落 wait/exec 计数（Prometheus 采样验证）。"""
    from prometheus_client import REGISTRY

    def _count(name: str) -> float:
        v = REGISTRY.get_sample_value(name, {"pool": "inner"})
        return v or 0.0

    wait_before = _count("rag_pool_wait_seconds_count")
    task_before = _count("rag_pool_task_seconds_count")

    fut = submit_rag_task("inner", lambda: 42)
    assert fut.result(timeout=5) == 42

    assert _count("rag_pool_wait_seconds_count") == wait_before + 1
    assert _count("rag_pool_task_seconds_count") == task_before + 1


def test_submit_rag_task_propagates_eval_identity_trace_and_budget(monkeypatch):
    """内层向量任务保留评测身份、Trace 与预算上下文。"""
    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget as budget_mod
    from backend.infra.llm.proxy import (
        _thread_local_user_id,
        scoped_current_user_id,
    )
    from backend.observability.llm_usage_store import current_usage_attribution
    from backend.observability.tracer import trace_collector

    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: object(),
    )
    trace_collector.clear_for_test()

    def read_context():
        attribution = current_usage_attribution()
        budget = budget_mod.current_request_budget()
        trace = trace_collector.current()
        return {
            "tenant_id": attribution["tenant_id"],
            "user_id": attribution["user_id"],
            "trace_id": attribution["trace_id"],
            "proxy_user_id": _thread_local_user_id(),
            "budget_id": budget_mod._current_request_id.get(),
            "budget_tenant_id": budget.tenant_id if budget else "",
        }

    try:
        with scoped_current_user_id("eval-worker"):
            with scoped_tool_identity(
                user_id="eval-worker", tenant_id="eval",
            ):
                trace = trace_collector.start(
                    question="context propagation", session_id="eval-session",
                )
                with budget_mod.scoped_request_budget(
                    "eval:thread-pool-context",
                    user_id="eval-worker",
                    tenant_id="eval",
                ):
                    actual = submit_rag_task("inner", read_context).result(timeout=5)

        assert actual == {
            "tenant_id": "eval",
            "user_id": "eval-worker",
            "trace_id": trace.id,
            "proxy_user_id": "eval-worker",
            "budget_id": "eval:thread-pool-context",
            "budget_tenant_id": "eval",
        }
    finally:
        trace_collector.clear_for_test()

    after_scope = submit_rag_task("inner", read_context).result(timeout=5)
    assert after_scope == {
        "tenant_id": "",
        "user_id": "",
        "trace_id": "",
        "proxy_user_id": None,
        "budget_id": "",
        "budget_tenant_id": "",
    }


def test_submit_rag_task_rejects_unknown_pool():
    import pytest

    with pytest.raises(ValueError, match="未知检索池"):
        submit_rag_task("nonexistent", lambda: 1)


def test_multi_query_fanout_wired_to_dedicated_pool(monkeypatch):
    """MQ fan-out 的父任务必须提交到 multi_query 池（接线回归锁）。"""
    import backend.infra.thread_pools as tp
    import backend.rag.retrieval.multi_query as mq
    from types import SimpleNamespace

    captured: list[str] = []
    real_submit = tp.submit_rag_task

    def _spy(pool_name, fn, /, *args, **kwargs):
        captured.append(pool_name)
        return real_submit(pool_name, fn, *args, **kwargs)

    monkeypatch.setattr(tp, "submit_rag_task", _spy)
    # 绕过 LLM：门控恒开 + 改写返回 3 个变体
    monkeypatch.setattr(mq, "need_multi_query", lambda q: (True, "test-forced"))
    monkeypatch.setattr(
        mq, "_rewrite", lambda q: [q, "换个角度看这个问题", "补充背景后的问题"],
    )
    # RequestContext 缺席时给setter一个可写命名空间
    monkeypatch.setattr(
        mq, "get_context",
        lambda: SimpleNamespace(mq_triggered=False, mq_reason="", mq_variants=1, mq_filtered=1),
    )

    class _FakeRetriever:
        def invoke(self, q: str) -> list[Document]:
            return [Document(page_content=f"doc-{q}", metadata={"chunk_id": f"c-{q}"})]

    retriever = mq.MultiQueryRetriever(base_retriever=_FakeRetriever())
    docs = retriever.invoke("测试多查询问题")

    assert captured, "任务必须经 submit_rag_task 提交"
    assert captured[0] == "multi_query_rewrite"
    assert set(captured[1:]) == {"multi_query"}, f"父任务池错误: {captured}"
    assert len(docs) >= 1
