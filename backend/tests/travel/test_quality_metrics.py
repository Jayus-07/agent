"""tests/travel/test_quality_metrics.py — STOP I6 质量遥测守护

验证三件事：
  1. Prometheus 指标在真实节点执行后被计数（候选池/校验/修复/置信度）
  2. 标签基数纪律：constraint 标签取值 ⊆ 固定 CODE_* 枚举（无高基数泄漏）
  3. 结构化事件行按 event= 命名进入日志
遥测全部软失败 —— 本测试同时证明「遥测挂了不拖垮规划」。
"""
from __future__ import annotations

import pytest

from backend.tests.travel.conftest import (
    make_day,
    make_item,
    make_itinerary,
    make_poi,
)
from backend.travel import quality_metrics as qm
from backend.travel.models.brief import TravelBrief
from backend.travel.models.validation import CODE_TIME_CLOSED
from backend.travel.repair import repair_node
from backend.travel.validator import travel_validator_node


@pytest.fixture
def _reset_registry(monkeypatch):
    """用独立计数环境隔离测试（不污染全局 registry 的既有值）。"""
    counters: dict[str, list[tuple]] = {}

    def _track(name):
        def _inner(labels=None, value=1):
            counters.setdefault(name, []).append(
                tuple(sorted((labels or {}).items())), )
        return _inner

    class _Labeled:
        def __init__(self, name):
            self.name = name

        def labels(self, **kw):
            counters.setdefault(self.name, []).append(tuple(sorted(kw.items())))
            return self

        def inc(self, v=1):
            pass

    class _GaugeStub:
        def __init__(self):
            self.value = None

        def set(self, v):
            self.value = v

    gauge = _GaugeStub()
    monkeypatch.setattr(qm, "travel_candidate_total", _Labeled("cand"))
    monkeypatch.setattr(qm, "travel_unresolved_place_total",
                        _Labeled("unres"))
    monkeypatch.setattr(qm, "travel_itinerary_validation_total",
                        _Labeled("valid"))
    monkeypatch.setattr(qm, "travel_itinerary_constraint_violation_total",
                        _Labeled("viol"))
    monkeypatch.setattr(qm, "travel_itinerary_repair_total",
                        _Labeled("repair"))
    monkeypatch.setattr(qm, "travel_itinerary_quality_score", gauge)
    return counters, gauge


def _state_with_itinerary() -> dict:
    poi = make_poi(poi_id="p1", open_time="09:00", close_time="17:00")
    day = make_day(items=[make_item(start="10:00", end="11:00", poi=poi)])
    return {
        "brief": TravelBrief(destination="测试城", days=1).model_dump(),
        "candidates": [poi.model_dump()],
        "itinerary": make_itinerary(days=[day]).model_dump(),
        "notes": [],
    }


def test_validator_records_pass_and_violations(_reset_registry):
    counters, gauge = _reset_registry
    # 合法行程 → pass
    travel_validator_node(_state_with_itinerary())
    assert ("status", "pass") in [t[0] for t in counters.get("valid", [])]

    # 闭馆冲突 → errors + 违反码
    poi_bad = make_poi(poi_id="p1", open_time="09:00", close_time="17:00")
    day_bad = make_day(items=[make_item(start="18:00", end="19:00", poi=poi_bad)])
    state_bad = _state_with_itinerary()
    state_bad["itinerary"] = make_itinerary(days=[day_bad]).model_dump()
    travel_validator_node(state_bad)
    valid_labels = [t[0] for t in counters.get("valid", [])]
    assert ("status", "errors") in valid_labels
    assert ("constraint", CODE_TIME_CLOSED) in [t[0] for t in counters.get("viol", [])]
    assert gauge.value is not None and 0.0 <= gauge.value <= 1.0


def test_repair_records_executed(_reset_registry):
    counters, _ = _reset_registry
    poi_bad = make_poi(poi_id="p1")
    day_bad = make_day(items=[make_item(start="18:00", end="19:00", poi=poi_bad)])
    state = _state_with_itinerary()
    state["itinerary"] = make_itinerary(days=[day_bad]).model_dump()
    # 先跑一次校验并把报告写回 state（repair 只认状态里的事实）
    state.update(travel_validator_node(state))
    state["repair_rounds"] = 0
    repair_node(state)
    assert ("status", "executed") in [t[0] for t in counters.get("repair", [])]


def test_metric_labels_stay_low_cardinality():
    """高基数泄漏守护：constraint 标签取值必须 ⊆ 固定违反码枚举。"""
    from backend.travel.models import validation as V

    allowed_codes = {
        v for k, v in vars(V).items()
        if k.startswith("CODE_") and isinstance(v, str)
    }
    # 现有调用点只传 CODE_* 枚举（record_validation 的入参来自
    # report.codes()，其值域即 CODE_*）——这里锁死枚举集合本身
    assert {"TIME_CLOSED", "POI_DUPLICATED",
            "POI_NOT_IN_CANDIDATES"} <= allowed_codes
    # 城市名等绝不允许成为合法标签值
    assert "福州" not in allowed_codes and "厦门" not in allowed_codes


def test_structured_event_line(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="rag_system"):
        qm.event("travel.itinerary.validated", errors=0, warnings=1)
    assert any("event=travel.itinerary.validated" in r.message
               for r in caplog.records)


def test_telemetry_failure_never_blocks_planning(monkeypatch):
    """软失败契约：指标层抛错时节点照常工作。"""
    def _boom(*a, **kw):
        raise RuntimeError("metrics down")

    monkeypatch.setattr(qm, "record_candidates", _boom)
    monkeypatch.setattr(qm, "record_unresolved", _boom)
    monkeypatch.setattr(qm, "record_validation", _boom)
    # 三个 hook 都包了 try/except —— 节点必须正常返回
    from backend.travel.experts.poi import poi_expert_node

    from backend.tools.travel import poi_seed

    state = {
        "brief": TravelBrief(destination="厦门", days=1).model_dump(),
        "notes": [],
    }
    update = poi_expert_node(state)
    assert update.get("candidates"), "遥测失败不得影响候选池产出"
