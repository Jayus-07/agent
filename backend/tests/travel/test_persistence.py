"""tests/travel/test_persistence.py — 持久化可见性与运行埋点（Phase 4，任务书 §10/§11）

覆盖：
1. _build_checkpointer 三态判定：disabled / degraded（postgres 失败降级）/ healthy
2. TRAVEL_REQUIRE_PERSISTENCE 强持久化策略：降级时拒绝复用跨轮产物（每轮全新规划）
3. persistence_status 事实链：slot_filler 落 state → supervisor_decision 携带 → reporter 披露
4. Run Trace：专家 span（成功/失败）、LBS 调用 span、reporter 的 TravelPlanRun 汇总 span
"""
from __future__ import annotations

import pytest

from backend.config import travel as T
from backend.tests.travel.conftest import make_day, make_item, make_itinerary, make_poi
from backend.travel.graph_builder import (
    PERSISTENCE_DEGRADED,
    PERSISTENCE_DISABLED,
    PERSISTENCE_HEALTHY,
    _build_checkpointer,
    get_persistence_status,
)
from backend.travel.graph_state import (
    load_itinerary,
    new_travel_graph_input,
    save_itinerary,
    save_validation,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.repair import repair_itinerary  # noqa: F401 — 保证模块可导入
from backend.travel.slot_filler import slot_filler_node
from backend.travel.supervisor import travel_supervisor_node
from backend.travel.validator import check_itinerary, travel_validator_node

from .conftest import MONDAY


def _state_with_previous(**extra) -> dict:
    """模拟「checkpointer 恢复出上一轮产物」的状态：需求未变、行程还在。"""
    brief = TravelBrief(destination="福州", days=2)
    itin = make_itinerary(brief=brief, days=[make_day(
        items=[make_item(poi=make_poi(), start="09:00", end="10:30")])])
    state = new_travel_graph_input("福州2天行程")
    state.update({
        "brief": brief.model_dump(),
        "brief_fingerprint": __import__(
            "backend.travel.graph_state", fromlist=["brief_fingerprint"]
        ).brief_fingerprint(brief),
        "candidates": [{"poi_id": "p1"}],
        "itinerary": save_itinerary(itin),
        "validation": save_validation(check_itinerary(itin)),
        "expert_history": [{"expert": "poi"}, {"expert": "transit"}],
    })
    state.update(extra)
    return state


# =============================================
# _build_checkpointer：三态判定
# =============================================


class TestBuildCheckpointerStatus:
    def test_disabled_when_flag_off(self, monkeypatch):
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", False)
        checkpointer, status = _build_checkpointer()
        assert (checkpointer, status) == (None, PERSISTENCE_DISABLED)

    def test_degraded_when_postgres_unavailable(self, monkeypatch):
        """postgres 连接失败 → 降级 MemorySaver，状态必须是 degraded。"""
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "postgres")
        import psycopg

        def _boom(*args, **kwargs):
            raise RuntimeError("pg down")

        monkeypatch.setattr(psycopg.Connection, "connect", _boom)
        checkpointer, status = _build_checkpointer()
        assert status == PERSISTENCE_DEGRADED
        assert checkpointer is not None  # 降级产物 MemorySaver，不是裸跑

    def test_healthy_when_postgres_ready(self, monkeypatch):
        """postgres 就绪 → healthy。mock 连接与 Saver，不真连库。"""
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "postgres")
        import psycopg
        from langgraph.checkpoint import postgres as lg_pg

        monkeypatch.setattr(psycopg.Connection, "connect",
                            lambda *args, **kwargs: object())
        fake_saver = type("FakeSaver", (), {"setup": lambda self: None})
        monkeypatch.setattr(lg_pg, "PostgresSaver", lambda conn: fake_saver())
        import backend.orchestration.graph.checkpointer_cleanup as cleanup
        monkeypatch.setattr(cleanup, "start_cleanup_daemon", lambda *a, **k: None)

        checkpointer, status = _build_checkpointer()
        assert status == PERSISTENCE_HEALTHY

    def test_status_reader_reads_module_singleton(self):
        assert get_persistence_status() in (
            PERSISTENCE_HEALTHY, PERSISTENCE_DEGRADED, PERSISTENCE_DISABLED)


# =============================================
# TRAVEL_REQUIRE_PERSISTENCE：强持久化策略
# =============================================


class TestRequirePersistence:
    def test_degraded_with_require_resets_cross_turn_products(self, monkeypatch):
        """降级 + 强持久化策略 → 拒绝复用跨轮产物，每轮按全新规划处理。"""
        from backend.travel import graph_builder

        monkeypatch.setattr(graph_builder, "_persistence_status", PERSISTENCE_DEGRADED)
        monkeypatch.setattr(T, "TRAVEL_REQUIRE_PERSISTENCE", True)
        state = _state_with_previous()

        update = slot_filler_node(state)

        assert update["persistence_status"] == PERSISTENCE_DEGRADED
        # 跨轮产物被无条件清空（即使指纹未变化）
        assert update.get("itinerary", "missing-key") in (None, "missing-key")
        assert update["validation"] is None
        assert update["expert_history"] == []
        assert any("全新规划" in n for n in update["notes"])

    def test_degraded_without_require_keeps_products(self, monkeypatch):
        """默认策略（不强持久化）：降级只披露、不改变跨轮行为。"""
        from backend.travel import graph_builder

        monkeypatch.setattr(graph_builder, "_persistence_status", PERSISTENCE_DEGRADED)
        monkeypatch.setattr(T, "TRAVEL_REQUIRE_PERSISTENCE", False)
        state = _state_with_previous()

        update = slot_filler_node(state)

        assert update["persistence_status"] == PERSISTENCE_DEGRADED
        assert "itinerary" not in update  # 未触碰跨轮产物
        assert not any("全新规划" in n for n in update["notes"])

    def test_healthy_with_require_keeps_products(self, monkeypatch):
        """强持久化策略 + 后端健康 → 跨轮改单照常。"""
        from backend.travel import graph_builder

        monkeypatch.setattr(graph_builder, "_persistence_status", PERSISTENCE_HEALTHY)
        monkeypatch.setattr(T, "TRAVEL_REQUIRE_PERSISTENCE", True)
        state = _state_with_previous()

        update = slot_filler_node(state)

        assert "itinerary" not in update
        assert not any("全新规划" in n for n in update["notes"])


# =============================================
# persistence_status 事实链
# =============================================


class TestPersistenceFacts:
    def test_status_flows_into_supervisor_decision(self, monkeypatch):
        from backend.travel import graph_builder

        monkeypatch.setattr(graph_builder, "_persistence_status", PERSISTENCE_DEGRADED)
        state = _state_with_previous(persistence_status=PERSISTENCE_DEGRADED,
                                     step_count=0, supervisor_decision={})
        cmd = travel_supervisor_node(state)
        facts = cmd.update["supervisor_decision"]
        assert facts["persistence_status"] == PERSISTENCE_DEGRADED

    def test_missing_status_defaults_to_empty(self):
        """状态里没有该键（旧 checkpoint 恢复）时不得崩，读空串。"""
        state = _state_with_previous(step_count=0, supervisor_decision={})
        state.pop("persistence_status", None)
        cmd = travel_supervisor_node(state)
        assert cmd.update["supervisor_decision"]["persistence_status"] == ""

    def test_travel_context_carries_status(self, monkeypatch):
        from backend.travel import graph_builder

        monkeypatch.setattr(graph_builder, "_persistence_status", PERSISTENCE_HEALTHY)
        state = _state_with_previous()
        update = slot_filler_node(state)
        assert update["persistence_status"] == PERSISTENCE_HEALTHY


# =============================================
# Run Trace（任务书 §11）
# =============================================


@pytest.fixture
def active_trace():
    """提供活跃 trace 上下文（span 埋点的软失败路径在无 trace 时为 noop）。"""
    from backend.observability.tracer import trace_collector
    trace_collector.clear_for_test()
    record = trace_collector.start("测试", session_id="t-p4",
                                   workflow_name="travel_test")
    yield record
    trace_collector.clear_for_test()


def _spans_by_id(record, span_id: str):
    return [s for s in record.spans if s.span_id.startswith(span_id)]


class TestExpertSpan:
    def test_success_span_recorded(self, active_trace):
        from backend.travel.experts.base import run_expert_safely

        result = run_expert_safely("poi", lambda s: {"data": {"x": 1}}, {})

        assert result["status"] == "success"
        spans = _spans_by_id(active_trace, "travel_expert_poi")
        assert len(spans) == 1
        span = spans[0]
        assert span.status == "success"
        assert span.metrics["expert_status"] == "success"
        assert span.duration_ms >= 0

    def test_failure_span_recorded_as_error(self, active_trace):
        from backend.travel.experts.base import run_expert_safely

        def _boom(state):
            raise RuntimeError("排程炸了")

        result = run_expert_safely("transit", _boom, {})

        assert result["status"] == "failed"
        spans = _spans_by_id(active_trace, "travel_expert_transit")
        assert len(spans) == 1
        assert spans[0].status == "error"
        assert "排程炸了" in spans[0].metrics["error"]

    def test_span_soft_fails_without_trace(self):
        """无活跃 trace：不抛错（noop span），专家照常执行。"""
        from backend.travel.experts.base import run_expert_safely

        result = run_expert_safely("budget", lambda s: {"data": {}}, {})
        assert result["status"] == "success"


class TestLbsSpan:
    def test_direction_span_success(self, active_trace, monkeypatch):
        from backend.tools.travel import live_map

        # _LEG_CACHE 是模块级缓存（TTL 15 分钟），跨测试共享——先清空，
        # 否则前一次运行残留的同坐标缓存会绕过 api.direction，span 打不出来
        live_map._LEG_CACHE.clear()
        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(
            live_map.api, "direction",
            lambda mode, a, b, c, d: {"distance_km": 5.0, "duration_min": 10,
                                      "taxi_fare_cny": 15},
        )
        got = live_map.live_leg(26.0, 119.0, 26.05, 119.05)
        assert got is not None and got["source"] == "tencent:lbs"

        spans = _spans_by_id(active_trace, "travel_lbs_direction")
        assert len(spans) == 1
        assert spans[0].status == "success"
        assert spans[0].metrics["distance_km"] == 5.0

    def test_direction_span_error_when_no_route(self, active_trace, monkeypatch):
        from backend.tools.travel import live_map

        # success 测试已把同坐标结果写进 _LEG_CACHE——不清空会直接命中缓存
        # 返回 5.0km，根本不会调 api.direction（实测踩过：断言 None 拿到 dict）
        live_map._LEG_CACHE.clear()
        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map.api, "direction",
                            lambda mode, a, b, c, d: None)
        assert live_map.live_leg(26.0, 119.0, 26.05, 119.05) is None

        spans = _spans_by_id(active_trace, "travel_lbs_direction")
        assert spans and spans[0].status == "error"
        assert spans[0].metrics["reason"] == "no_route"

    def test_place_search_span(self, active_trace, monkeypatch):
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map.api, "place_search",
                            lambda name, region=None, page_size=5: [
                                {"id": "x1", "name": "测试点", "lat": 26.0,
                                 "lng": 119.0, "category": "旅游景点:国家级景点"}])
        monkeypatch.setattr(live_map.api, "resolve_district",
                            lambda city: None)

        poi = live_map.resolve_place("测试点", "福州")
        assert poi is not None and poi.verification_status == "unverified"

        spans = _spans_by_id(active_trace, "travel_lbs_place_search")
        assert len(spans) == 1
        assert spans[0].status == "success"
        assert spans[0].metrics["hits"] == 1


def _full_state(persistence: str) -> dict:
    """构造走到 reporter 的完整 state（出单已就绪）。模块级：多测试类共用。"""
    brief = TravelBrief(destination="福州", days=1)
    itin = make_itinerary(brief=brief, days=[make_day(
        items=[make_item(poi=make_poi(), start="09:00", end="10:30")])])
    itin.stamp_version(brief)
    state = new_travel_graph_input("福州1天行程")
    state.update({
        "brief": brief.model_dump(),
        "candidates": [{"poi_id": "p1"}],
        "itinerary": save_itinerary(itin),
        "validation": save_validation(check_itinerary(itin)),
        "persistence_status": persistence,
    })
    return state


class TestPlanRunSpan:

    def test_plan_run_span_carries_run_facts(self, active_trace):
        from backend.travel.reporter import travel_reporter_node

        update = travel_reporter_node(_full_state(PERSISTENCE_HEALTHY))
        assert update["final_answer"]

        spans = _spans_by_id(active_trace, "travel_plan_run")
        assert len(spans) == 1
        metrics = spans[0].metrics
        assert metrics["destination"] == "福州"
        assert metrics["plan_version"] == 1
        assert metrics["persistence_status"] == PERSISTENCE_HEALTHY
        assert metrics["errors"] == 0

    def test_noop_without_trace(self):
        """无活跃 trace 时出单不受影响（软失败）。"""
        from backend.travel.reporter import travel_reporter_node

        update = travel_reporter_node(_full_state(PERSISTENCE_DISABLED))
        assert update["final_answer"]


class TestDegradedDisclosure:
    def test_degraded_mentioned_in_answer(self):
        from backend.travel.reporter import travel_reporter_node

        update = travel_reporter_node(_full_state(PERSISTENCE_DEGRADED))
        assert "临时存储" in update["final_answer"]

    def test_healthy_not_mentioned(self):
        from backend.travel.reporter import travel_reporter_node

        update = travel_reporter_node(_full_state(PERSISTENCE_HEALTHY))
        assert "临时存储" not in update["final_answer"]
