"""tests/travel/test_node_span.py — 域图节点 span 装饰器（M14 / D14）

锁：traced_node 生命周期（start→fn→end）/ metrics_fn 提取与容错 / 节点异常
语义不变（error 收口后原样上抛）/ 软失败（collector 异常不穿透）/ 三个域图
节点（slot_filler/supervisor/repair）已实际包装。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def collector(monkeypatch):
    import backend.observability.tracer as tracer_mod

    fake = MagicMock()
    monkeypatch.setattr(tracer_mod, "trace_collector", fake)
    return fake


def _make(**kw):
    from backend.travel.node_span import traced_node

    @traced_node("t_node", "测试节点", **kw)
    def node(state):
        return {"stage": "ok", "n": len(state.get("items") or [])}

    return node


class TestTracedNode:
    def test_success_lifecycle_with_metrics(self, collector):
        node = _make(metrics_fn=lambda u: {"stage_len": len(u["stage"])})
        out = node({"items": [1, 2]})
        assert out == {"stage": "ok", "n": 2}
        start = collector.start_span.call_args
        assert start.args[0] == "t_node"
        assert start.kwargs["name"] == "测试节点"
        end = collector.end_span.call_args
        assert end.kwargs["status"] == "success"
        assert end.kwargs["metrics"] == {"stage_len": 2}

    def test_metrics_fn_failure_is_soft(self, collector):
        """metrics_fn 抛异常按无 metrics 收口，节点返回值不受影响。"""
        def bad(_u):
            raise KeyError("x")

        node = _make(metrics_fn=bad)
        out = node({})
        assert out["stage"] == "ok"
        assert collector.end_span.call_args.kwargs["status"] == "success"

    def test_node_exception_closes_error_and_reraises(self, collector):
        from backend.travel.node_span import traced_node

        @traced_node("t_fail", "失败节点")
        def node(_state):
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            node({})
        assert collector.end_span.call_args.kwargs["status"] == "error"

    def test_collector_failure_is_soft(self, collector):
        collector.start_span.side_effect = RuntimeError("no trace")
        node = _make()
        assert node({})["stage"] == "ok"
        collector.end_span.assert_not_called()

    def test_wraps_preserves_identity(self, collector):
        node = _make()
        assert node.__name__ == "node"
        assert hasattr(node, "__wrapped__")


class TestDomainNodesWired:
    """三个此前无 span 的域图节点（D14 缺口）必须已实际包装。"""

    def test_slot_filler_wrapped(self):
        from backend.travel.slot_filler import slot_filler_node

        assert hasattr(slot_filler_node, "__wrapped__")

    def test_supervisor_wrapped(self):
        from backend.travel.supervisor import travel_supervisor_node

        assert hasattr(travel_supervisor_node, "__wrapped__")

    def test_repair_wrapped(self):
        from backend.travel.repair import repair_node

        assert hasattr(repair_node, "__wrapped__")
