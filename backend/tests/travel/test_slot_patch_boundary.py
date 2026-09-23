"""tests/travel/test_slot_patch_boundary.py — STOP I2 slot 边界与 avoid-PATCH 路由

覆盖任务书 T3/T4/T7 与 STOP H Deferred #1/#2 的回归面：
  - 裸数字预算：「预算改成5000」（无货币单位）必须抽出 5000（T3）
  - 抽取边界不回归：「3天」仍不是钱；「预算5000元」「预算两万」原口径不变
  - lodging 城市混淆：「住在厦门」不进 lodging；「白城沙滩厦门」剥城市尾缀
  - avoid-PATCH 信号与路由通道：completed 态「不去鼓浪屿了」短路回旅游域（T4），
    「不去厦门了，重新规划杭州两天」仍走 NEW_RUN（T7），取消/客服优先级不破
"""
import pytest

from backend.travel.slot_filler import (
    extract_avoid,
    extract_budget,
    extract_lodging,
    is_avoid_patch_query,
    is_cancel_run_query,
    is_new_run_query,
)
from backend.orchestration.context.travel_pending_resolver import (
    resolve_travel_pending,
)


# ============================================================
# 预算抽取边界（T3）
# ============================================================
class TestBudgetExtractionBoundary:
    def test_bare_number_after_budget_keyword(self):
        # STOP H Deferred #2 主场景：T3「预算改成5000」此前抽不出
        assert extract_budget("预算改成5000") == 5000.0
        assert extract_budget("预算5000") == 5000.0
        assert extract_budget("预算大概 3000") == 3000.0

    def test_currency_forms_unchanged(self):
        assert extract_budget("预算5000元") == 5000.0
        assert extract_budget("预算两万") == 20000.0
        assert extract_budget("预算2万") == 20000.0

    def test_no_budget_keyword_still_rejected(self):
        # 铁律不破：没有「预算」语境，裸数字依旧不是钱
        assert extract_budget("福州3天") is None
        assert extract_budget("玩5天，2个人") is None
        assert extract_budget("3个人") is None


# ============================================================
# lodging 抽取边界（T8 / STOP H Deferred #2）
# ============================================================
class TestLodgingExtractionBoundary:
    def test_city_is_not_lodging(self):
        # 「住在厦门」表达的是目的地，不是住宿区
        assert extract_lodging("住在厦门") == ""
        assert extract_lodging("住杭州") == ""

    def test_city_suffix_stripped(self):
        # STOP H 实测「白城沙滩厦门」：城市尾缀剥掉
        assert extract_lodging("住在白城沙滩厦门") == "白城沙滩"

    def test_normal_lodging_unchanged(self):
        assert extract_lodging("住难波") == "难波"
        assert extract_lodging("住在梅田") == "梅田"
        assert extract_lodging("住白城沙滩附近") == "白城沙滩"

    def test_question_form_still_rejected(self):
        assert extract_lodging("住宿在哪") == ""


# ============================================================
# avoid-PATCH 信号（T4）与优先级（T6/T7）
# ============================================================
class TestAvoidPatchSignal:
    def test_local_exclusion_is_avoid_patch(self):
        assert is_avoid_patch_query("不去鼓浪屿了")
        assert is_avoid_patch_query("不想去三坊七巷了")
        assert is_avoid_patch_query("避开鼓浪屿")

    def test_no_place_no_signal(self):
        # 没有具体地点的「不想去了」不是 avoid-PATCH（防误拦）
        assert not is_avoid_patch_query("不想去了")

    def test_cancel_takes_priority(self):
        assert is_cancel_run_query("取消这次行程规划")
        assert not is_avoid_patch_query("取消这次行程规划")

    def test_new_run_takes_priority(self):
        # T7：目的地变更属于 NEW_RUN，不是 avoid（「厦门」被名录挡在 avoid 外）
        assert is_new_run_query("不去厦门了，重新规划杭州两天")
        assert not is_avoid_patch_query("不去厦门了，重新规划杭州两天")

    def test_length_guard(self):
        assert not is_avoid_patch_query("不" + "去鼓浪屿" * 20 + "了")

    def test_extract_avoid_covers_poi_canonical_name(self):
        # 「不去鼓浪屿了」→ avoid 含 canonical 名「鼓浪屿」
        assert "鼓浪屿" in extract_avoid("不去鼓浪屿了")


# ============================================================
# 路由通道：TravelPendingResolver（completed 态，无 pending）
# ============================================================
def _ctx(active_domain="travel", run_id="run-1", stage="completed",
         pending=None):
    return {
        "active_domain": active_domain,
        "conversation_id": "conv-1",
        "brief_summary": {
            "travel_run_id": run_id,
            "travel_stage": stage,
            "travel_pending": pending or {},
        },
    }


class TestResolverAvoidPatchChannel:
    def test_completed_state_avoid_patch_routes_to_travel(self):
        # STOP H Deferred #1 主场景：出单后（completed、pending 已清）
        update = resolve_travel_pending("不去鼓浪屿了", _ctx())
        assert update is not None
        assert update["route_mode"] == "travel"
        assert update["travel_context"]["travel_route"]["resume_mode"] == "patch_avoid"
        assert update["travel_context"]["travel_route"]["new_run"] is False

    def test_no_active_run_never_intercepts(self):
        # 无活跃 run：哪怕句式吻合也不拦（交回正常路由）
        assert resolve_travel_pending("不去鼓浪屿了", _ctx(run_id="")) is None

    def test_cancelled_run_never_intercepts(self):
        assert resolve_travel_pending(
            "不去鼓浪屿了", _ctx(stage="cancelled")) is None

    def test_non_travel_domain_never_intercepts(self):
        assert resolve_travel_pending(
            "不去鼓浪屿了", _ctx(active_domain="customer_service")) is None

    def test_cs_strong_signal_yields(self):
        # CS 优先铁律：客服强信号在场让位（「不去鼓浪屿了」+ 订单双信号）
        cs_msg = "不去鼓浪屿了，我的订单什么时候发货，退款怎么办理"
        assert resolve_travel_pending(cs_msg, _ctx()) is None

    def test_plain_slot_answer_unchanged(self):
        # 既有 pending 补槽通道不回归
        update = resolve_travel_pending(
            "三天", _ctx(stage="slot",
                         pending={"question_id": "q1", "requested_slots": ["days"]}))
        assert update is not None
        assert update["travel_context"]["travel_route"]["resume_mode"] == "continue"

    def test_unrelated_message_still_passes_through(self):
        assert resolve_travel_pending("今天天气怎么样", _ctx()) is None
