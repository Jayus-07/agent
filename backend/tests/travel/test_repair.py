"""tests/travel/test_repair.py — 修复器取舍纪律

最关键的一条：**用户点名必去的条目永不被静默丢弃**。
必去项与事实的冲突分两路（Phase 3，任务书 §7）：
  - 时段冲突（营业窗口外 / 闭馆日）→ validator 判 decision_required，
    不进修复队列，由 reporter 单列请用户取舍；
  - 其他 error 级违反点到必去项（如时间重叠）→ 修复器保留并记为
    kept_required，由 reporter 如实告知。
其余用例覆盖各类违规的动作选择、防震荡签名与终止判定。
"""
from __future__ import annotations

from backend.config import travel as T
from backend.tests.travel.conftest import (
    MONDAY,
    make_day,
    make_item,
    make_itinerary,
    make_poi,
)
from backend.travel.graph_state import save_itinerary, save_validation
from backend.travel.models.brief import TravelBrief
from backend.travel.models.validation import (
    CODE_TIME_OVERLAP,
    LEVEL_DECISION_REQUIRED,
    ValidationReport,
    Violation,
)
from backend.travel.repair import (
    constraint_signature,
    plan_signature,
    repair_itinerary,
    repair_node,
)
from backend.travel.validator import check_itinerary


class TestClosedTimeRepair:
    def test_drops_non_required_item(self):
        poi = make_poi(poi_id="late", name="深夜馆", open_time="09:00",
                       close_time="17:00")
        keep = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                        close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=keep),
                make_item(title="深夜馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        names = [i.title for d in repaired.days for i in d.items]
        assert "深夜馆" not in names
        assert "正常馆" in names
        assert any("深夜馆" in a.dropped for a in actions)
        assert repaired.repair_rounds == 1

    def test_required_closed_time_is_user_decision_not_error(self):
        """必去项时段冲突改道 decision_required（任务书 §7）。

        自动修复只有「删掉必去」（违背用户明确诉求）和「保留冲突」（违背
        事实）两条路，该由用户选 —— 因此它不进修复队列、不挡交付。
        """
        poi = make_poi(poi_id="must", name="必去馆", open_time="09:00",
                       close_time="17:00", required=True)
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, must_go=["必去馆"]),
            days=[make_day(items=[
                make_item(title="必去馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        report = check_itinerary(it)
        closed = [v for v in report.violations if v.code == "TIME_CLOSED"]
        assert closed and closed[0].level == LEVEL_DECISION_REQUIRED
        assert closed[0].detail["required"] is True
        assert report.errors == []            # 不进修复队列
        assert report.decision_required       # 但必须单列
        assert report.passed is True          # 不挡交付
        # 修复器收不到该违反：无可执行动作
        repaired, actions = repair_itinerary(it, report)
        assert repaired is None and actions == []

    def test_required_item_is_kept_and_logged(self):
        """error 级违反点到必去项 → 保留并记 kept_required（不删）。"""
        must = make_poi(poi_id="must", name="必去馆", open_time="09:00",
                        close_time="17:00", required=True)
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, must_go=["必去馆"]),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="必去馆", start="10:00", end="11:30", poi=must),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        kept = [a for a in actions if a.kept_required]
        assert kept and kept[0].kept_required == ["必去馆"]
        assert kept[0].code == CODE_TIME_OVERLAP
        # 必去项未被丢弃 → 无需重建行程
        assert repaired is None

    def test_closed_weekday_repair(self):
        poi = make_poi(poi_id="mon", name="周一闭馆馆",
                       closed_weekdays=(0,), open_time="09:00", close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(day_date=MONDAY, items=[
                make_item(title="周一闭馆馆", start="09:00", end="10:30", poi=poi),
            ])],
        )
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        assert repaired.days == []


class TestPaceAndBudgetRepair:
    def test_pace_repair_drops_lowest_rated(self):
        pois = [
            make_poi(poi_id=f"p{i}", name=f"P{i}", rating=4.0 + i * 0.1,
                     suggested_minutes=100)
            for i in range(T.TRAVEL_PACE_MAX_POIS["moderate"] + 1)
        ]
        items = [make_item(title=p.name, start=f"{9+i:02d}:00",
                           end=f"{10+i:02d}:40", poi=p)
                 for i, p in enumerate(pois)]
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, pace="moderate"),
            days=[make_day(items=items)],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        # 最低分的 p0 应被移除
        assert any("P0" in a.dropped for a in actions)
        remaining = [i.title for d in repaired.days for i in d.items]
        assert "P0" not in remaining

    def test_budget_repair_drops_most_expensive_ticket(self):
        cheap = make_poi(poi_id="cheap", name="便宜馆", ticket_cny=20.0,
                         open_time="09:00", close_time="17:00")
        pricey = make_poi(poi_id="pricey", name="昂贵馆", ticket_cny=800.0,
                          open_time="09:00", close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, budget_cny=100),
            days=[make_day(items=[
                make_item(title="便宜馆", start="09:00", end="10:00", poi=cheap),
                make_item(title="昂贵馆", start="10:00", end="11:00", poi=pricey),
            ])],
        )
        # 预算校验读的是行程自身的费用汇总（而非重算），故需显式写入
        it.cost.tickets = 820.0
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        assert any("昂贵馆" in a.dropped for a in actions)
        remaining = [i.title for d in repaired.days for i in d.items]
        assert "便宜馆" in remaining and "昂贵馆" not in remaining
        # 修复后费用按幸存项目重算，不再带着已删除项目的票价
        assert repaired.cost.tickets == 20.0

    def test_budget_repair_skips_free_items(self):
        """超预算但全是免费项目 → 无可移除项，返回 None（交由 reporter 说明）。"""
        free = make_poi(poi_id="free", name="免费馆", ticket_cny=0.0)
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, budget_cny=1),
            days=[make_day(items=[
                make_item(title="免费馆", start="09:00", end="10:00", poi=free),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is None
        assert actions == []

    def test_nothing_to_repair_returns_none(self):
        poi = make_poi(open_time="09:00", close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(start="09:00", end="10:00", poi=poi)])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is None and actions == []


class TestRepairPreservesContract:
    def test_repair_preserves_day_index_and_date(self):
        poi = make_poi(poi_id="bad", name="超晚馆", open_time="09:00",
                       close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=2),
            days=[
                make_day(day_index=1, day_date=MONDAY, items=[
                    make_item(title="正常馆", start="09:00", end="10:30", poi=ok)]),
                make_day(day_index=2, day_date=None, items=[
                    make_item(title="超晚馆", start="18:00", end="19:30", poi=poi)]),
            ],
        )
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        # 第 2 天被清空 → 只保留第 1 天，且 day_index/date 未被重排
        assert len(repaired.days) == 1
        assert repaired.days[0].day_index == 1
        assert repaired.days[0].day_date == MONDAY

    def test_repair_recomputes_cost(self):
        poi = make_poi(poi_id="bad", name="超晚馆", ticket_cny=100.0,
                       open_time="09:00", close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="超晚馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        before = it.days[0].cost_cny
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired.cost.total > 0
        assert repaired.days[0].cost_cny != before

    def test_repair_carries_sources_and_warnings(self):
        poi = make_poi(poi_id="bad", name="超晚馆", open_time="09:00",
                       close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="超晚馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        it.sources = ["seed:local"]
        it.warnings = ["示例数据声明"]
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired.sources == ["seed:local"]
        assert repaired.warnings == ["示例数据声明"]


class TestStalledRepairIsTerminal:
    """修复节点必须把「无自动修复手段」写成一个 supervisor 读得到的事实。

    P0 回归：该分支原先只写 ``stage="report"``，而 supervisor **不看 stage**、
    只认状态事实，于是被反复调起直到 GraphRecursionError。
    """

    def test_unfixable_marks_stalled(self):
        """error 级违反点到必去项（时间重叠）→ 无动作可执行 → 必须打上终止标记。

        必去项的时段冲突已改道 decision_required（不进修复），此处的
        「无自动手段」路径由 error 级违反点到必去项触发：修复器只能
        kept_required 保留，没有任何可执行动作。
        """
        must = make_poi(poi_id="must", name="必去馆", open_time="09:00",
                        close_time="17:00", required=True)
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, must_go=["必去馆"]),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="必去馆", start="10:00", end="11:30", poi=must),
            ])],
        )
        state = {"itinerary": save_itinerary(it),
                 "validation": save_validation(check_itinerary(it))}

        out = repair_node(state)

        assert out["repair_stalled"] is True
        assert out["stage"] == "report"
        # 行程原样保留（必去项不可删），只把说明写进 notes 交由 reporter 披露
        assert "itinerary" not in out
        assert any("无法自动调整" in n for n in out["notes"])

    def test_successful_repair_clears_stalled(self):
        """有动作真正执行时必须解除标记，否则后续轮次会被误判成终态。"""
        late = make_poi(poi_id="late", name="深夜馆", open_time="09:00",
                        close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="深夜馆", start="18:00", end="19:30", poi=late),
            ])],
        )
        state = {"itinerary": save_itinerary(it),
                 "validation": save_validation(check_itinerary(it)),
                 "repair_stalled": True, "repair_rounds": 0}

        out = repair_node(state)

        assert out["repair_stalled"] is False
        assert out["stage"] == "validate"
        assert out["repair_rounds"] == 1


# ============================================================
# 防震荡签名（任务书 §6）
# ============================================================
def _error(code: str = "GEO_SCATTER", day: int = 1, poi_id: str = "p1") -> Violation:
    return Violation(code=code, level="error", message="x", day_index=day,
                     detail={"poi_id": poi_id})


class TestConstraintSignature:
    def test_same_violation_set_same_signature(self):
        """违反集相同（顺序无关）→ 签名必须相同 —— 这是「修没修好」的判据。"""
        a = ValidationReport(violations=[
            _error("TIME_CLOSED", day=1, poi_id="p1"),
            _error("GEO_SCATTER", day=2, poi_id="p2"),
        ])
        b = ValidationReport(violations=[
            _error("GEO_SCATTER", day=2, poi_id="p2"),
            _error("TIME_CLOSED", day=1, poi_id="p1"),
        ])
        assert constraint_signature(a) == constraint_signature(b)

    def test_violation_set_change_changes_signature(self):
        a = ValidationReport(violations=[_error(poi_id="p1")])
        b = ValidationReport(violations=[_error(poi_id="p3")])
        assert constraint_signature(a) != constraint_signature(b)

    def test_only_errors_count(self):
        """warning / decision_required 不进修复队列，也不构成「修没修好」。"""
        base = ValidationReport(violations=[_error()])
        noisy = ValidationReport(violations=[
            _error(),
            Violation(code="TIME_DAY_OVERRUN", level="warning", message="x"),
            Violation(code="TIME_CLOSED", level=LEVEL_DECISION_REQUIRED,
                      message="x", detail={"poi_id": "must"}),
        ])
        assert constraint_signature(base) == constraint_signature(noisy)

    def test_plan_signature_tracks_visit_sequence(self):
        brief = TravelBrief(destination="测试城", days=1)
        a = make_itinerary(brief=brief, days=[make_day(items=[
            make_item(poi=make_poi(poi_id="p1")),
            make_item(poi=make_poi(poi_id="p2")),
        ])])
        b = make_itinerary(brief=brief, days=[make_day(items=[
            make_item(poi=make_poi(poi_id="p1")),
        ])])
        assert plan_signature(a) != plan_signature(b)
        # 同一形态两次计算稳定
        assert plan_signature(a) == plan_signature(a)


class TestOscillationGuard:
    """连续两轮违反集无变化 → 停止重复尝试（任务书 §6 防震荡）。"""

    def _repairable_state(self) -> dict:
        late = make_poi(poi_id="late", name="深夜馆", open_time="09:00",
                        close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="深夜馆", start="18:00", end="19:30", poi=late),
            ])],
        )
        report = check_itinerary(it)
        return {"itinerary": save_itinerary(it),
                "validation": save_validation(report)}, report

    def test_two_stale_rounds_stall_repair(self):
        """上轮修复前的违反集与本轮相同（streak=1）→ 本轮再无改善即终止。"""
        state, report = self._repairable_state()
        state["last_repair_constraint_sig"] = constraint_signature(report)
        state["repair_no_improvement_streak"] = 1

        out = repair_node(state)

        assert out["repair_stalled"] is True
        assert out["stage"] == "report"
        assert "itinerary" not in out
        assert any("连续两轮" in n for n in out["notes"])
        assert out["repair_log"][-1]["no_improvement"] is True
        assert "停止重复尝试" in out["repair_log"][-1]["reason"]

    def test_improvement_resets_streak(self):
        """违反集签名与上轮不同 → 上轮有改善 → 不 stall，正常修复并重置计数。"""
        state, _ = self._repairable_state()
        state["last_repair_constraint_sig"] = "deadbeef"  # 与本轮签名不同
        state["repair_no_improvement_streak"] = 1

        out = repair_node(state)

        assert out["repair_stalled"] is False
        assert out["stage"] == "validate"
        assert out["repair_no_improvement_streak"] == 0
        assert out["last_repair_constraint_sig"] != "deadbeef"
