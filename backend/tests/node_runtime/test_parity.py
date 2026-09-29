"""tests/node_runtime/test_parity.py — STOP F 迁移 Parity（旧路径 vs 新路径）

同一函数分别跑「迁移前手写实现」（本文件内逐字节快照，仅改名）与
「迁移后 run_expert_safely（NodeRunner 路径）」，比较：
status / error / duration_ms（均为非负整数且同量级）/ data 包装 / metrics 打点 /
travel span 形态。快照基线 = main@1136481 的两个 experts/base.py。
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from backend.customer_service.experts.base import (
    ExpertStatus,
    run_expert_safely as new_cs_run_expert_safely,
)
from backend.travel.experts.base import (
    TravelExpertStatus,
    run_expert_safely as new_travel_run_expert_safely,
)


# ==========================================================================
# 迁移前实现快照（parity 基准 oracle）—— 逻辑与 main@1136481 逐字节一致，
# 仅改名 + 剥离日志行（日志不在 parity 比较范围，结果/打点/span 才是）
# ==========================================================================


def legacy_cs_run_expert_safely(expert_name, fn, state, timeout_s=None):
    from backend.observability.metrics import record_cs_expert_result

    t0 = time.monotonic()
    if timeout_s is not None and timeout_s > 0:
        import concurrent.futures
        import contextvars

        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"cs-expert-{expert_name}",
        )
        ctx = contextvars.copy_context()
        try:
            future = pool.submit(ctx.run, fn, state)
            result = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            future.cancel()
            record_cs_expert_result(expert_name, "timeout")
            return {
                "expert": expert_name,
                "status": ExpertStatus.TIMEOUT.value,
                "response_draft": "",
                "error": f"expert timed out after {timeout_s}s",
                "duration_ms": duration_ms,
            }
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            record_cs_expert_result(expert_name, "failed")
            return {
                "expert": expert_name,
                "status": ExpertStatus.FAILED.value,
                "response_draft": "",
                "error": str(e),
                "duration_ms": duration_ms,
            }
        finally:
            pool.shutdown(wait=False)
    else:
        try:
            result = fn(state)
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            record_cs_expert_result(expert_name, "failed")
            return {
                "expert": expert_name,
                "status": ExpertStatus.FAILED.value,
                "response_draft": "",
                "error": str(e),
                "duration_ms": duration_ms,
            }

    duration_ms = int((time.monotonic() - t0) * 1000)

    status = result.get("status", ExpertStatus.SUCCESS.value)
    result.setdefault("expert", expert_name)
    result.setdefault("status", status)
    result["duration_ms"] = duration_ms

    record_cs_expert_result(expert_name, status)
    return result


def legacy_travel_run_expert_safely(expert_name, fn, state):
    t0 = time.monotonic()
    span = legacy_start_expert_span(expert_name)
    try:
        result = fn(state)
        duration_ms = int((time.monotonic() - t0) * 1000)
        result.setdefault("expert", expert_name)
        result.setdefault("status", TravelExpertStatus.SUCCESS.value)
        result["duration_ms"] = duration_ms
        legacy_end_expert_span(span, result["status"], duration_ms)
        return result
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        legacy_end_expert_span(span, TravelExpertStatus.FAILED.value,
                               duration_ms, error=str(e))
        # 旧实现的 TravelExpertResult(...) 运行时就是普通 dict
        return dict(
            expert=expert_name,
            status=TravelExpertStatus.FAILED.value,
            data={},
            notes=[],
            error=str(e),
            duration_ms=duration_ms,
        )


def legacy_start_expert_span(expert_name):
    """快照自旧 travel/experts/base.py::_start_expert_span（软失败）。"""
    try:
        from backend.observability.tracer import trace_collector
        return trace_collector.start_span(
            f"travel_expert_{expert_name}", name=f"旅游专家:{expert_name}",
            type="agent", kind="agent", input={},
        )
    except Exception:
        return None


def legacy_end_expert_span(span, status: str, duration_ms: int,
                           error: str = "") -> None:
    """快照自旧 travel/experts/base.py::_end_expert_span（软失败）。"""
    if span is None:
        return
    try:
        from backend.observability.tracer import trace_collector
        metrics = {"expert_status": status, "duration_ms": duration_ms}
        if error:
            metrics["error"] = error
        trace_collector.end_span(
            span, output={"status": status}, metrics=metrics,
            status="error" if status == TravelExpertStatus.FAILED.value
            else "success",
        )
    except Exception:
        pass


def _without_duration(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "duration_ms"}


def _assert_result_parity(legacy: dict, new: dict, *, duration_slack_ms: int = 300):
    assert _without_duration(legacy) == _without_duration(new), (
        f"parity 破坏:\nlegacy={legacy}\nnew={new}"
    )
    assert set(legacy.keys()) == set(new.keys())
    for key in ("duration_ms",):
        if key in legacy:
            assert isinstance(legacy[key], int) and legacy[key] >= 0
            assert isinstance(new[key], int) and new[key] >= 0
            assert abs(legacy[key] - new[key]) <= duration_slack_ms


# ==========================================================================
# CS 专家 parity
# ==========================================================================


class TestCsParity:
    @pytest.mark.parametrize("timeout_s", [None, 5])
    def test_success_parity(self, timeout_s):
        def fn(state):
            return {"response_draft": "hello", "status": "success",
                    "data": {"a": 1}}

        legacy = legacy_cs_run_expert_safely("knowledge", fn, {"x": 1},
                                             timeout_s=timeout_s)
        new = new_cs_run_expert_safely("knowledge", fn, {"x": 1},
                                       timeout_s=timeout_s)
        _assert_result_parity(legacy, new)

    def test_success_without_explicit_status_parity(self):
        def fn(state):
            return {"response_draft": "ok"}

        legacy = legacy_cs_run_expert_safely("action", fn, {})
        new = new_cs_run_expert_safely("action", fn, {})
        _assert_result_parity(legacy, new)
        assert legacy["status"] == "success" == new["status"]

    @pytest.mark.parametrize("timeout_s", [None, 5])
    def test_exception_parity(self, timeout_s):
        def fn(state):
            raise ValueError("boom")

        legacy = legacy_cs_run_expert_safely("query", fn, {},
                                             timeout_s=timeout_s)
        new = new_cs_run_expert_safely("query", fn, {}, timeout_s=timeout_s)
        _assert_result_parity(legacy, new)
        assert legacy["status"] == "failed" == new["status"]
        assert legacy["error"] == "boom" == new["error"]

    def test_timeout_parity(self):
        def fn(state):
            time.sleep(0.6)
            return {"response_draft": "never"}

        legacy = legacy_cs_run_expert_safely("knowledge", fn, {}, timeout_s=0.15)
        new = new_cs_run_expert_safely("knowledge", fn, {}, timeout_s=0.15)
        _assert_result_parity(legacy, new, duration_slack_ms=1000)
        assert legacy["status"] == "timeout" == new["status"]
        assert legacy["error"] == new["error"] == "expert timed out after 0.15s"

    def test_timeout_exception_inside_maps_to_failed_parity(self):
        """限时窗口内 fn 自身异常：新旧都判 failed（非 timeout）。"""
        def fn(state):
            raise ValueError("inner boom")

        legacy = legacy_cs_run_expert_safely("action", fn, {}, timeout_s=5)
        new = new_cs_run_expert_safely("action", fn, {}, timeout_s=5)
        _assert_result_parity(legacy, new)
        assert legacy["status"] == "failed" == new["status"]

    def test_metrics_call_parity(self):
        """@patch 注入下新旧打点序列一致（lazy import 保证 patch 生效）。"""
        def fn_ok(state):
            return {"status": "success"}

        def fn_bad(state):
            raise RuntimeError("fail")

        with patch("backend.observability.metrics.record_cs_expert_result") as m:
            legacy_cs_run_expert_safely("knowledge", fn_ok, {})
            legacy_calls = list(m.mock_calls)
            m.reset_mock()
            new_cs_run_expert_safely("knowledge", fn_ok, {})
            assert list(m.mock_calls) == legacy_calls

            m.reset_mock()
            legacy_cs_run_expert_safely("knowledge", fn_bad, {})
            legacy_calls = list(m.mock_calls)
            m.reset_mock()
            new_cs_run_expert_safely("knowledge", fn_bad, {})
            assert list(m.mock_calls) == legacy_calls

            m.reset_mock()

            # fn 内 sleep 保证稳定命中超时窗口
            def fn_slow(state):
                time.sleep(0.6)
                return {}

            legacy_cs_run_expert_safely("knowledge", fn_slow, {}, timeout_s=0.1)
            legacy_calls = list(m.mock_calls)
            m.reset_mock()
            new_cs_run_expert_safely("knowledge", fn_slow, {}, timeout_s=0.1)
            assert list(m.mock_calls) == legacy_calls


# ==========================================================================
# Travel 专家 parity
# ==========================================================================


class TestTravelParity:
    def test_success_parity(self):
        def fn(state):
            return {"data": {"pois": ["a"]}, "notes": ["n1"]}

        legacy = legacy_travel_run_expert_safely("poi", fn, {})
        new = new_travel_run_expert_safely("poi", fn, {})
        _assert_result_parity(legacy, new)
        assert legacy["expert"] == "poi" == new["expert"]
        assert legacy["status"] == "success" == new["status"]

    def test_fn_status_passthrough_parity(self):
        """fn 自报非 success 状态（如 skipped）：新旧都原样保留。"""
        def fn(state):
            return {"data": {}, "status": "skipped"}

        legacy = legacy_travel_run_expert_safely("budget", fn, {})
        new = new_travel_run_expert_safely("budget", fn, {})
        _assert_result_parity(legacy, new)
        assert legacy["status"] == "skipped" == new["status"]

    def test_failure_parity(self):
        def fn(state):
            raise RuntimeError("排程炸了")

        legacy = legacy_travel_run_expert_safely("transit", fn, {})
        new = new_travel_run_expert_safely("transit", fn, {})
        _assert_result_parity(legacy, new)
        # 失败结果的字段契约冻结：恰含六字段，data/notes 为空容器
        assert set(new.keys()) == {"expert", "status", "data", "notes",
                                   "error", "duration_ms"}
        assert new["data"] == {} and new["notes"] == []
        assert new["error"] == "排程炸了"

    @pytest.fixture
    def active_trace(self):
        from backend.observability.tracer import trace_collector
        trace_collector.clear_for_test()
        record = trace_collector.start("parity", session_id="t-stopf",
                                       workflow_name="node_runtime_parity")
        yield record
        trace_collector.clear_for_test()

    def _expert_spans(self, record, name):
        return [s for s in record.spans if s.span_id.startswith(f"travel_expert_{name}")]

    def test_span_parity_success(self, active_trace):
        from backend.observability.tracer import trace_collector

        def fn(state):
            return {"data": {"x": 1}}

        legacy_travel_run_expert_safely("poi", fn, {})
        legacy_spans = self._expert_spans(active_trace, "poi")

        trace_collector.clear_for_test()
        record = trace_collector.start("parity", session_id="t-stopf",
                                       workflow_name="node_runtime_parity")
        new_travel_run_expert_safely("poi", fn, {})
        new_spans = self._expert_spans(record, "poi")

        assert len(legacy_spans) == len(new_spans) == 1
        old, new = legacy_spans[0], new_spans[0]
        assert old.span_id.startswith("travel_expert_poi")
        assert new.span_id == old.span_id
        assert new.name == old.name
        assert new.type == old.type == "agent"
        assert new.kind == old.kind
        assert new.status == old.status == "success"
        assert new.metrics["expert_status"] == old.metrics["expert_status"]
        assert new.metrics["duration_ms"] >= 0 and old.metrics["duration_ms"] >= 0

    def test_span_parity_failure(self, active_trace):
        from backend.observability.tracer import trace_collector

        def fn(state):
            raise RuntimeError("排程炸了")

        legacy_travel_run_expert_safely("transit", fn, {})
        legacy_spans = self._expert_spans(active_trace, "transit")

        trace_collector.clear_for_test()
        record = trace_collector.start("parity", session_id="t-stopf",
                                       workflow_name="node_runtime_parity")
        new_travel_run_expert_safely("transit", fn, {})
        new_spans = self._expert_spans(record, "transit")

        assert len(legacy_spans) == len(new_spans) == 1
        old, new = legacy_spans[0], new_spans[0]
        assert new.status == old.status == "error"
        assert new.metrics["expert_status"] == "failed"
        assert "排程炸了" in new.metrics["error"]

    def test_span_soft_fail_without_trace_parity(self):
        """无活跃 trace：新旧都不抛错（noop span），专家照常执行。"""
        def fn(state):
            return {"data": {}}

        legacy = legacy_travel_run_expert_safely("budget", fn, {})
        new = new_travel_run_expert_safely("budget", fn, {})
        _assert_result_parity(legacy, new)
