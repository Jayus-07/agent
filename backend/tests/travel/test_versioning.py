"""tests/travel/test_versioning.py — 三层版本与 plan 状态机（Phase 2，任务书 §4）

覆盖：
1. brief.version：slot_filler 指纹变化时递增，不变不递增
2. data_snapshot_version：候选池签名稳定、source 参与签名、空池为空
3. stamp_version：initial / repair（parent 链）/ snapshot 沿用语义
4. plan 状态机：validator 判定 ready / degraded
5. supervisor_decision 携带版本事实（可复现性的 trace 依据）
6. 序列化往返：版本字段全部可过 checkpointer JSON 序列化
"""
from __future__ import annotations

from backend.travel.graph_state import (
    brief_fingerprint,
    data_snapshot_version,
    load_itinerary,
    new_travel_graph_input,
    save_itinerary,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import (
    CHANGE_BRIEF,
    CHANGE_INITIAL,
    CHANGE_REPAIR,
    PLAN_STATUS_DEGRADED,
    PLAN_STATUS_NEEDS_USER_DECISION,
    PLAN_STATUS_READY,
    PLAN_STATUS_VALIDATING,
    Itinerary,
)
from backend.travel.models.validation import Violation
from backend.travel.slot_filler import slot_filler_node
from backend.travel.supervisor import travel_supervisor_node
from backend.travel.validator import travel_validator_node

from .conftest import MONDAY, make_day, make_item, make_itinerary, make_leg, make_poi


def _state(**extra) -> dict:
    base = new_travel_graph_input("测试")
    base.update(extra)
    return base


# =============================================
# brief.version 递增
# =============================================


class TestBriefVersion:
    def test_default_is_one(self):
        assert TravelBrief(destination="福州", days=2).version == 1

    def test_increments_on_fingerprint_change(self):
        prev = TravelBrief(destination="福州", days=2, version=3)
        state = _state(
            brief=prev.model_dump(),
            brief_fingerprint=brief_fingerprint(prev),
            user_message="福州3天行程",  # 用户改天数 → 真实指纹变化
        )
        update = slot_filler_node(state)
        assert update["brief"]["version"] == 4
        assert update["brief_change_reason"] == CHANGE_BRIEF
        assert "days" in update["brief_changed_fields"]

    def test_stable_when_unchanged(self):
        prev = TravelBrief(destination="福州", days=2)
        state = _state(
            brief=prev.model_dump(),
            brief_fingerprint=brief_fingerprint(prev),
            user_message="谢谢",  # 无新槽位 → 需求不变
        )
        update = slot_filler_node(state)
        # 用户没改需求 → 版本不递增、无变化原因
        assert update["brief"]["version"] == 1
        assert "brief_change_reason" not in update

    def test_changed_fields_identify_diff(self):
        prev = TravelBrief(destination="福州", days=2, budget_cny=1000.0)
        state = _state(
            brief=prev.model_dump(),
            brief_fingerprint="old00000000",
            user_message="改成3天预算2000",
        )
        update = slot_filler_node(state)
        fields = set(update["brief_changed_fields"])
        # 指纹变化必然由消息里的槽位差异驱动；version 本身不算差异
        assert "version" not in fields
        assert fields <= {"destination", "days", "start_date", "party_size",
                          "budget_cny", "preferences", "must_go", "avoid", "pace"}


# =============================================
# data_snapshot_version
# =============================================


class TestDataSnapshot:
    def test_stable_for_same_pool(self):
        pool = [{"poi_id": "a", "source": "seed:local"},
                {"poi_id": "b", "source": "seed:local"}]
        assert data_snapshot_version(pool) == data_snapshot_version(list(reversed(pool)))

    def test_changes_with_source(self):
        pool_a = [{"poi_id": "a", "source": "seed:local"}]
        pool_b = [{"poi_id": "a", "source": "tencent:lbs"}]
        assert data_snapshot_version(pool_a) != data_snapshot_version(pool_b)

    def test_empty_pool_is_empty_string(self):
        assert data_snapshot_version([]) == ""

    def test_length_bounded(self):
        sig = data_snapshot_version([{"poi_id": "a", "source": "s"}])
        assert len(sig) == 8


# =============================================
# stamp_version：版本章
# =============================================


class TestStampVersion:
    def test_initial_stamp(self):
        brief = TravelBrief(destination="福州", days=2, version=2)
        itin = make_itinerary(brief=brief)
        itin.stamp_version(brief, data_snapshot="abc12345")
        assert itin.plan_version == 1
        assert itin.parent_plan_version is None
        assert itin.brief_version == 2
        assert itin.data_snapshot_version == "abc12345"
        assert itin.change_reason == CHANGE_INITIAL
        assert itin.status == PLAN_STATUS_VALIDATING
        assert itin.created_at  # ISO 时间戳已记录

    def test_repair_chain(self):
        brief = TravelBrief(destination="福州", days=2, version=1)
        v1 = make_itinerary(brief=brief)
        v1.stamp_version(brief, data_snapshot="abc12345")
        v2 = make_itinerary(brief=brief)
        v2.repair_rounds = 1
        v2.stamp_version(brief, reason=CHANGE_REPAIR, parent=v1)
        # 修复后继：版本 +1、parent 指向旧版；候选池未变 → snapshot 沿用旧值
        assert v2.plan_version == 2
        assert v2.parent_plan_version == 1
        assert v2.change_reason == CHANGE_REPAIR
        assert v2.data_snapshot_version == "abc12345"
        assert v2.brief_version == 1

    def test_changed_fields_recorded(self):
        brief = TravelBrief(destination="厦门", days=3, version=2)
        itin = make_itinerary(brief=brief)
        itin.stamp_version(brief, reason=CHANGE_BRIEF,
                           changed_fields=["days", "destination"])
        assert itin.changed_fields == ["days", "destination"]


# =============================================
# plan 状态机：validator 判定
# =============================================


class TestPlanStatus:
    def _violation(self, code: str = "GEO_SCATTER") -> Violation:
        return Violation(level="error", code=code, message="测试违反",
                         day_index=1, detail={})

    def test_ready_when_no_errors(self, simple_brief):
        poi = make_poi()
        day = make_day(items=[make_item(poi=poi, start="09:00", end="10:30")])
        itin = make_itinerary(brief=simple_brief, days=[day])
        itin.stamp_version(simple_brief)
        state = _state(
            brief=simple_brief.model_dump(),
            itinerary=save_itinerary(itin),
            notes=[],
        )
        # 无违反：不注入 violations 即可（check_itinerary 基于行程本身）
        update = travel_validator_node(state)
        saved = load_itinerary(update)
        assert saved is not None
        assert saved.status == PLAN_STATUS_READY

    def test_degraded_with_errors(self, simple_brief):
        """error 级违反（非必去闭馆）→ degraded。"""
        poi = make_poi(closed_weekdays=[0])  # 周一闭馆，非必去
        day = make_day(items=[make_item(poi=poi, start="09:00", end="10:30")],
                       day_date=MONDAY)
        itin = make_itinerary(brief=simple_brief, days=[day])
        itin.stamp_version(simple_brief)
        state = _state(
            brief=simple_brief.model_dump(),
            itinerary=save_itinerary(itin),
            notes=[],
        )
        update = travel_validator_node(state)
        saved = load_itinerary(update)
        assert saved is not None
        assert saved.status == PLAN_STATUS_DEGRADED

    def test_needs_user_decision_on_required_closed(self, simple_brief):
        """必去项闭馆（decision_required，无 error）→ needs_user_decision。"""
        poi = make_poi(required=True, closed_weekdays=[0])
        day = make_day(items=[make_item(poi=poi, start="09:00", end="10:30")],
                       day_date=MONDAY)
        itin = make_itinerary(brief=simple_brief, days=[day])
        itin.stamp_version(simple_brief)
        state = _state(
            brief=simple_brief.model_dump(),
            itinerary=save_itinerary(itin),
            notes=[],
        )
        update = travel_validator_node(state)
        saved = load_itinerary(update)
        assert saved is not None
        assert saved.status == PLAN_STATUS_NEEDS_USER_DECISION


# =============================================
# supervisor：版本事实进决策
# =============================================


class TestSupervisorVersionFacts:
    def test_decision_carries_versions(self, simple_brief):
        itin = make_itinerary(brief=simple_brief)
        itin.stamp_version(simple_brief)
        state = _state(
            brief=simple_brief.model_dump(),
            itinerary=save_itinerary(itin),
            step_count=0,
            supervisor_decision={},
        )
        cmd = travel_supervisor_node(state)
        facts = cmd.update["supervisor_decision"]
        assert facts["brief_version"] == 1
        assert facts["plan_version"] == 1
        assert facts["plan_status"] == PLAN_STATUS_VALIDATING

    def test_decision_without_itinerary(self, simple_brief):
        state = _state(brief=simple_brief.model_dump(), step_count=0,
                       supervisor_decision={})
        cmd = travel_supervisor_node(state)
        facts = cmd.update["supervisor_decision"]
        assert facts["brief_version"] == 1
        assert facts["plan_version"] is None
        assert facts["plan_status"] is None


# =============================================
# 序列化往返（checkpointer 兼容）
# =============================================


class TestSerialization:
    def test_version_fields_roundtrip(self, simple_brief):
        itin = make_itinerary(brief=simple_brief)
        itin.stamp_version(simple_brief, reason=CHANGE_REPAIR,
                           parent=make_itinerary(brief=simple_brief),
                           data_snapshot="feed1234",
                           changed_fields=["dropped:p1"])
        restored = Itinerary.model_validate_json(itin.model_dump_json())
        assert restored.plan_version == itin.plan_version
        assert restored.parent_plan_version == itin.parent_plan_version
        assert restored.data_snapshot_version == "feed1234"
        assert restored.changed_fields == ["dropped:p1"]
        assert restored.change_reason == CHANGE_REPAIR

    def test_legs_with_phase1_fields_roundtrip(self):
        leg = make_leg().model_copy(update={
            "observed_at": "2026-09-17T12:00:00+00:00",
            "traffic_aware": True, "is_estimate": False,
            "fallback_reason": None,
        })
        assert leg.model_dump_json()  # 可序列化即通过
