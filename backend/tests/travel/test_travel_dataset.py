"""tests/travel/test_travel_dataset.py — 旅游评测数据集与 runner（Phase 5，任务书 §15）

覆盖：
1. 数据集完整性：manifest 与 cases.jsonl 一致、A-F 六组齐全、tier 合法
2. runner 判定契约 _judge_one：逐字段断言行为
3. Failure 组假 Provider 注入：块内生效、finally 无条件恢复
4. 抽样真实图执行：happy path 与用户决策各 1 条（不追求全量——全量由
   评测 CLI 跑，2026-09-18 校准 22/22 pass）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.evaluation.dataset.loader import DATASET_DIR, load_dataset
# 别名导入：避免 pytest 把 eval 体系的 TestCase 误当测试类收集（CollectionWarning）
from backend.evaluation.models import TestCase as EvalTestCase  # noqa: F401
from backend.evaluation.runners.travel import (
    _ProviderFailure,
    _judge_one,
    _run_case,
    _snapshot_actual,
)

TRAVEL_DIR = DATASET_DIR / "travel"
VALID_TIERS = {"smoke", "core", "hard", "regression"}


# =============================================
# 一、数据集完整性
# =============================================
class TestDatasetIntegrity:
    def test_manifest_matches_cases(self):
        manifest = json.loads((TRAVEL_DIR / "manifest.json").read_text("utf-8"))
        lines = [
            ln for ln in (TRAVEL_DIR / "cases.jsonl").read_text("utf-8").splitlines()
            if ln.strip()
        ]
        assert manifest["module"] == "travel"
        assert manifest["case_count"] == len(lines)
        assert load_dataset("travel") .__len__() == len(lines)

    def test_groups_a_f_covered(self):
        cases = load_dataset("travel")
        groups = {c.metadata.get("group") for c in cases}
        assert groups == {"A", "B", "C", "D", "E", "F"}

    def test_tiers_valid_and_expected_present(self):
        cases = load_dataset("travel")
        for c in cases:
            assert c.metadata.get("tier") in VALID_TIERS, f"{c.id} tier 非法"
            assert c.expected, f"{c.id} expected 为空"
            assert c.question.strip(), f"{c.id} question 为空"

    def test_multiturn_cases_have_followup_contract(self):
        """跨轮组：same_thread 必须配 followup 消息；followup_expected 可空。"""
        for c in load_dataset("travel"):
            if c.metadata.get("group") != "C":
                continue
            assert c.metadata.get("same_thread") is True, f"{c.id} 缺 same_thread"
            assert c.metadata.get("followup"), f"{c.id} 缺 followup 消息"

    def test_failure_cases_declare_injection(self):
        """Failure 组的失败用例必须显式声明注入点，防止静默失效。"""
        for c in load_dataset("travel"):
            if c.metadata.get("group") != "F":
                continue
            assert (
                c.metadata.get("fail_transit") or c.metadata.get("fail_poi")
            ), f"{c.id} 未声明任何 Provider 失败注入"


# =============================================
# 二、判定契约
# =============================================
def _act(**overrides) -> dict:
    base = {
        "status": "ready", "destination": "福州", "days": 2, "party_size": 2,
        "pace": "moderate", "plan_version": 2, "parent_plan_version": 1,
        "repair_rounds": 1, "decision_required": False, "error_violations": 0,
        "violation_codes": ["BUDGET_OVER"], "confidence": 0.6,
        "final_answer": "# 福州 2 天行程\n\n*行程 v2*", "notes": ["需求已变化"],
        "has_itinerary": True, "clarification": False,
    }
    base.update(overrides)
    return base


class TestJudgeOne:
    def test_empty_expected_passes(self):
        assert _judge_one({}, _act(), "t") == []

    def test_status_and_scalar_mismatches(self):
        reasons = _judge_one({"status": "degraded", "days": 3}, _act(), "t")
        assert len(reasons) == 2

    def test_version_chain_and_repair(self):
        assert _judge_one(
            {"min_plan_version": 2, "parent_linked": True, "repair_triggered": True},
            _act(), "t") == []
        reasons = _judge_one(
            {"min_plan_version": 3, "repair_triggered": False}, _act(), "t")
        assert len(reasons) == 2

    def test_confidence_cap(self):
        assert _judge_one({"confidence_max": 0.7}, _act(), "t") == []
        assert _judge_one({"confidence_max": 0.5}, _act(), "t")

    def test_answer_and_notes_substrings(self):
        exp = {"must_contain": ["行程 v2"], "must_not_contain": ["需要你决定"],
               "notes_contain": ["需求已变化"], "notes_not_contain": ["临时存储"]}
        assert _judge_one(exp, _act(), "t") == []
        bad = _judge_one({"must_contain": ["不存在"], "notes_not_contain": ["需求已变化"]},
                         _act(), "t")
        assert len(bad) == 2

    def test_violation_codes_subset(self):
        assert _judge_one({"violation_codes": ["BUDGET_OVER"]}, _act(), "t") == []
        assert _judge_one({"violation_codes": ["MUST_GO_MISSING"]}, _act(), "t")


# =============================================
# 三、Provider 失败注入的恢复性
# =============================================
class TestProviderFailureInjection:
    def test_transit_injection_restored(self):
        from backend.tools.travel import routing

        sentinel = lambda *a, **k: None  # noqa: E731
        routing.set_route_provider(sentinel)
        try:
            with _ProviderFailure(fail_transit=True, fail_poi=False):
                assert routing._route_provider is not None
                assert routing._route_provider is not sentinel
                with pytest.raises(RuntimeError):
                    routing._route_provider("driving", 1, 2, 3, 4)
            assert routing._route_provider is sentinel
        finally:
            routing.set_route_provider(None)

    def test_poi_injection_restored(self):
        import backend.tools.travel.poi as poi_mod

        original = poi_mod.search_poi
        try:
            with _ProviderFailure(fail_transit=False, fail_poi=True):
                assert poi_mod.search_poi is not original
                with pytest.raises(RuntimeError):
                    poi_mod.search_poi("福州")
            assert poi_mod.search_poi is original
        finally:
            poi_mod.search_poi = original

    def test_noop_injection_untouched(self):
        import backend.tools.travel.poi as poi_mod
        from backend.tools.travel import routing

        before_provider = routing._route_provider
        before_poi = poi_mod.search_poi
        with _ProviderFailure(fail_transit=False, fail_poi=False):
            assert routing._route_provider is before_provider
            assert poi_mod.search_poi is before_poi


# =============================================
# 四、抽样真实图执行
# =============================================
class TestRunnerSmoke:
    def test_snapshot_shape(self):
        """_snapshot_actual 对缺键 state 安全（不抛错、键齐全）。"""
        act = _snapshot_actual({})
        assert act["status"] is None and act["repair_rounds"] == 0
        assert act["clarification"] is False and act["has_itinerary"] is False

    def test_happy_path_case_passes(self):
        cases = {c.id: c for c in load_dataset("travel")}
        result = _run_case(cases["T-A01"])
        assert result.status == "pass", result.error_msg

    def test_user_decision_case_passes(self):
        cases = {c.id: c for c in load_dataset("travel")}
        result = _run_case(cases["T-E01"])
        assert result.status == "pass", result.error_msg
