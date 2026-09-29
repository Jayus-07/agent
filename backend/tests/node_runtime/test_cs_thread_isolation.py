"""tests/node_runtime/test_cs_thread_isolation.py — CS 超时线程隔离语义锁（STOP F）

两次生产事故的回归门，语义迁移到 core/node_runtime 的
TimeoutStrategy.THREAD_ISOLATED（唯一实现）后必须原样保持：
- P2.3（2026-09-17 全量回归实证）：per-call 独立单 worker 池——共享池的孤儿
  任务长期占用 worker、后续调用排队导致 future.result 误判超时；本文件锁
  「专用命名线程执行 + 超时后下一次调用照常成功」。
- B4（2026-09-23 生产收口）：ThreadPoolExecutor.submit 不携带 contextvars，
  请求级状态（Context Budget 业务 pin）在线程内不可见；本文件锁
  「上下文拷贝进线程可见 + 线程内变更不回流调用方」。
"""
from __future__ import annotations

import contextvars
import threading
import time

from backend.customer_service.experts.base import run_expert_safely

_marker = contextvars.ContextVar("stopf_marker", default=None)


class TestThreadIsolation:
    def test_fn_runs_in_dedicated_pool_thread(self):
        """THREAD_ISOLATED：fn 在专用命名线程执行（非调用线程、非共享池）。"""
        seen = {}

        def fn(state):
            seen["thread"] = threading.current_thread()
            seen["main"] = threading.main_thread()
            return {"status": "success"}

        result = run_expert_safely("knowledge", fn, {}, timeout_s=5)
        assert result["status"] == "success"
        assert seen["thread"] is not seen["main"]
        assert seen["thread"].name.startswith("cs-expert-knowledge")

    def test_contextvars_copied_into_thread(self):
        """B4：调用方 contextvars 在限时线程内可见（copy_context）。"""
        token = _marker.set("outer")
        try:
            seen = {}

            def fn(state):
                seen["v"] = _marker.get()
                return {"status": "success"}

            run_expert_safely("knowledge", fn, {}, timeout_s=5)
            assert seen["v"] == "outer"
        finally:
            _marker.reset(token)

    def test_thread_mutation_does_not_leak_back(self):
        """B4 的另一面：线程内 set 只改拷贝，不污染调用方上下文。"""
        token = _marker.set("outer")
        try:
            def fn(state):
                _marker.set("inner")
                return {"status": "success"}

            run_expert_safely("knowledge", fn, {}, timeout_s=5)
            assert _marker.get() == "outer"
        finally:
            _marker.reset(token)

    def test_no_timeout_runs_in_caller_thread(self):
        """timeout_s=None 走 NONE 策略：与旧实现一致在调用线程内执行。"""
        seen = {}

        def fn(state):
            seen["thread"] = threading.current_thread()
            return {"status": "success"}

        run_expert_safely("query", fn, {})
        assert seen["thread"] is threading.current_thread()


class TestTimeoutBehavior:
    def test_timeout_returns_timeout_status(self):
        def slow(state):
            time.sleep(0.8)
            return {"response_draft": "never"}

        result = run_expert_safely("knowledge", slow, {}, timeout_s=0.15)
        assert result["expert"] == "knowledge"
        assert result["status"] == "timeout"
        assert result["error"] == "expert timed out after 0.15s"
        assert result["duration_ms"] >= 100

    def test_timeout_then_immediate_recovery(self):
        """P2.3 核心：超时后不残留池化 worker 阻塞后续调用。"""
        def slow(state):
            time.sleep(0.8)
            return {"status": "success"}

        first = run_expert_safely("knowledge", slow, {}, timeout_s=0.15)
        assert first["status"] == "timeout"

        started = time.monotonic()
        second = run_expert_safely("query", lambda s: {"status": "success"}, {})
        assert second["status"] == "success"
        # 快调用不被任何残留排队阻塞（共享池饿死场景下这里会显著放大）
        assert (time.monotonic() - started) < 2.0

    def test_timeout_exception_inside_maps_to_failed(self):
        """限时窗口内 fn 自身异常 → failed（非 timeout）。"""
        def bad(state):
            raise ValueError("inner boom")

        result = run_expert_safely("action", bad, {}, timeout_s=5)
        assert result["status"] == "failed"
        assert result["error"] == "inner boom"

    def test_invalid_timeout_values_degrade_to_no_timeout(self):
        """timeout_s 为 0/负值：与旧实现同语义，视作不限时。"""
        result = run_expert_safely("complaint", lambda s: {"ok": 1}, {},
                                   timeout_s=0)
        assert result["status"] == "success"
        result = run_expert_safely("complaint", lambda s: {"ok": 1}, {},
                                   timeout_s=-1)
        assert result["status"] == "success"
