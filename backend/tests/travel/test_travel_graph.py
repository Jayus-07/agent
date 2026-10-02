"""tests/travel/test_travel_graph.py — 调度决策、路由预过滤与端到端

调度器做成纯函数（decide）就是为了这里：不需要起 LangGraph 运行时
就能穷举各分支。端到端用例只验证「链路通了 + 结果确定 + 契约成立」，
细节正确性由 validator/repair 的单测负责。
"""
from __future__ import annotations

import pytest

from backend.config import travel as T
from backend.orchestration.graph.travel_prefilter import (
    is_travel_request,
    try_travel_prefilter,
)
from backend.travel.graph_builder import get_travel_graph
from backend.travel.graph_state import (
    new_travel_graph_input,
    save_itinerary,
    save_validation,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.graph_result import (
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NO_DATA,
    STATUS_SUCCESS,
    build_travel_graph_result,
)
from backend.travel.models.itinerary import Itinerary
from backend.travel.models.validation import (
    LEVEL_DECISION_REQUIRED,
    LEVEL_ERROR,
    ValidationReport,
    Violation,
)
from backend.travel.supervisor import (
    _STAGE_TO_ACTION,
    TravelDecision,
    TravelStage,
    decide,
)


def _state(**overrides) -> dict:
    state = new_travel_graph_input("测试请求")
    state.update(overrides)
    return state


def _empty_itinerary() -> Itinerary:
    return Itinerary(brief=TravelBrief(destination="测试城", days=1))


class TestSupervisorDecide:
    def test_missing_slots_goes_to_report(self):
        got = decide(_state(brief_missing=["destination"]))
        assert got.stage is TravelStage.REPORT
        assert "追问" in got.reason

    def test_fresh_state_starts_with_poi(self):
        assert decide(_state()).stage is TravelStage.POI

    def test_empty_candidate_pool_terminates(self):
        got = decide(_state(expert_history=[{"expert": "poi"}], candidates=[]))
        assert got.stage is TravelStage.REPORT

    def test_poi_done_goes_to_transit(self):
        got = decide(_state(expert_history=[{"expert": "poi"}], candidates=[{"poi_id": "a"}]))
        assert got.stage is TravelStage.TRANSIT

    def test_transit_without_itinerary_terminates(self):
        """排程没产出结果时不得无限重试 transit，直接收尾。"""
        got = decide(_state(
            expert_history=[{"expert": "poi"}, {"expert": "transit"}],
            candidates=[{"poi_id": "a"}], itinerary=None))
        assert got.stage is TravelStage.REPORT

    def test_stage_chain_progresses(self):
        state = _state(
            expert_history=[{"expert": "poi"}, {"expert": "transit"}],
            candidates=[{"poi_id": "a"}], itinerary=save_itinerary(_empty_itinerary()))
        # weather（2026-09-22）：transit 之后先做天气核查，再进预算
        assert decide(state).stage is TravelStage.WEATHER

        state["expert_history"] = state["expert_history"] + [{"expert": "weather"}]
        assert decide(state).stage is TravelStage.BUDGET

        state["expert_history"] = state["expert_history"] + [{"expert": "budget"}]
        assert decide(state).stage is TravelStage.RISK

        state["expert_history"] = state["expert_history"] + [{"expert": "risk"}]
        assert decide(state).stage is TravelStage.VALIDATE

    def test_passed_validation_goes_to_report(self):
        state = _state(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
            validation=save_validation(ValidationReport()),
        )
        got = decide(state)
        assert got.stage is TravelStage.REPORT
        assert "校验通过" in got.reason

    def test_failed_validation_triggers_repair(self):
        report = ValidationReport(violations=[Violation(
            code="TIME_CLOSED", level=LEVEL_ERROR, message="x")])
        state = _state(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
            validation=save_validation(report),
            repair_rounds=0,
        )
        assert decide(state).stage is TravelStage.REPAIR

    def test_repair_round_limit_terminates(self):
        report = ValidationReport(violations=[Violation(
            code="TIME_CLOSED", level=LEVEL_ERROR, message="x")])
        state = _state(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
            validation=save_validation(report),
            repair_rounds=T.TRAVEL_MAX_REPAIR_ROUNDS,
        )
        got = decide(state)
        assert got.stage is TravelStage.REPORT
        assert "上限" in got.reason

    def test_step_limit_forces_report(self):
        got = decide(_state(step_count=T.TRAVEL_MAX_STEPS))
        assert got.stage is TravelStage.REPORT

    def test_cleared_validation_revalidates(self):
        """修复节点清空 validation 后必须回到 VALIDATE，否则会把坏行程报成好行程。"""
        state = _state(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
            validation=None,
        )
        assert decide(state).stage is TravelStage.VALIDATE

    def test_stalled_repair_terminates(self):
        """修复器回报「无自动修复手段」后不得再回 REPAIR。

        回归用例（曾致 GraphRecursionError）：repair 的「无法修复」分支原先
        既不推进 repair_rounds 也不清 validation，而 supervisor 只认状态事实、
        不认上游声明的 stage → 永远判 REPAIR 原地打转，直到撞上
        LangGraph 的 recursion_limit，用户侧表现为「服务暂时不可用」且行程丢失。
        """
        report = ValidationReport(violations=[Violation(
            code="PACE_TOO_INTENSE", level=LEVEL_ERROR, message="x")])
        state = _state(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
            validation=save_validation(report),
            repair_rounds=0,          # 关键：轮数还是 0，按轮数判会误判回 REPAIR
            repair_stalled=True,
        )
        got = decide(state)
        assert got.stage is TravelStage.REPORT
        assert "自动修复" in got.reason

    def test_decision_required_does_not_trigger_repair(self):
        """必去项闭馆（decision_required，无 error）→ 直接 REPORT 请用户取舍。

        任务书 §7：decision_required 不进修复队列 —— supervisor 不得把它
        当成待修违反路由回 REPAIR（否则修复器只会原地报「无自动手段」）。
        """
        report = ValidationReport(violations=[Violation(
            code="TIME_CLOSED", level=LEVEL_DECISION_REQUIRED, message="x",
            detail={"poi_id": "must", "required": True})])
        state = _state(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
            validation=save_validation(report),
            repair_rounds=0,
        )
        got = decide(state)
        assert got.stage is TravelStage.REPORT
        assert "需你决定 1 项" in got.reason

    def test_action_naming_covers_all_stages(self):
        """每个 stage 都有结构化动作命名（trace/评测归因的粒度，任务书 §5）。"""
        for stage, expected in _STAGE_TO_ACTION.items():
            assert TravelDecision(stage, "").action == expected
        # 抽查关键动作语义
        assert TravelDecision(TravelStage.REPAIR, "").action == "run_repair"
        assert TravelDecision(TravelStage.REPORT, "").action == "finish_report"

    def test_decision_action_follows_routing(self):
        """decide 的实际产出也带 action：通过 → finish_report，修复 → run_repair。"""
        base = dict(
            expert_history=[{"expert": e} for e in ("poi", "transit", "weather", "budget", "risk")],
            candidates=[{"poi_id": "a"}],
            itinerary=save_itinerary(_empty_itinerary()),
        )
        passed = decide(_state(**base, validation=save_validation(ValidationReport())))
        assert passed.stage is TravelStage.REPORT
        assert passed.action == "finish_report"

        failed = decide(_state(
            **base,
            validation=save_validation(ValidationReport(violations=[Violation(
                code="TIME_CLOSED", level=LEVEL_ERROR, message="x")])),
            repair_rounds=0,
        ))
        assert failed.stage is TravelStage.REPAIR
        assert failed.action == "run_repair"


class TestTravelPrefilter:
    @pytest.fixture(autouse=True)
    def _reset_travel_switch_cache(self):
        """总闸经 sys_config 缓存动态读：前后各清，防污染本文件其它用例。

        背景（2026-09-30 修正）：M16（``2f5fbb5``）把旅游域总闸从
        ``config.travel.TRAVEL_ENABLED`` 常量改为 ``sys_config.get_mode``
        动态读，本次迁移的「存量测试基线修正」改了两个文件却漏了本文件——
        下面两条用例仍在 patch 已经没人读的常量，**patch 恒不生效**：
        「关闭」用例因此变成恒失败的空转守护（实测单跑即失败）。
        此处按 M16 既有口径（与 ``test_cs_prefilter_explicit`` 同款）改走
        sys_config 缓存，恢复该守护的真实拦截力。
        """
        from backend.services import sys_config

        sys_config.reset_cache_for_tests()
        yield
        sys_config.reset_cache_for_tests()

    def test_trip_request_detected(self):
        assert is_travel_request("帮我规划福州3天行程") is True

    def test_itinerary_keyword_with_city_detected(self):
        assert is_travel_request("厦门4天怎么玩") is True

    def test_business_query_not_detected(self):
        assert is_travel_request("统计本月订单金额") is False

    def test_generic_word_alone_not_detected(self):
        """「周末」这类口语词单独出现不得判为旅游，否则会抢走业务流量。"""
        assert is_travel_request("周末的订单量") is False

    def test_city_plus_days_detected(self):
        assert is_travel_request("福州2天，1个人，轻松一点") is True

    def test_business_time_window_not_detected(self):
        """「福州的近3天订单量」必须留在业务链路，不能被旅游域抢走。"""
        assert is_travel_request("福州的近3天订单量") is False
        assert is_travel_request("最近30天厦门的销量") is False

    def test_prefilter_disabled_returns_none(self):
        from backend.services import sys_config

        sys_config._values["TRAVEL_ENABLED"] = "false"  # M16：DB 覆盖关闭语义
        assert try_travel_prefilter("福州3天行程", {}) is None

    def test_prefilter_hit_sets_route_mode(self):
        from backend.services import sys_config

        sys_config._values["TRAVEL_ENABLED"] = "true"  # M16：经缓存开总闸
        got = try_travel_prefilter("帮我规划福州3天行程", {"session_id": "s1"})
        assert got is not None
        assert got["route_mode"] == "travel"
        assert got["travel_context"]["conversation_id"] == "s1"


class TestEndToEnd:
    def _invoke(self, message: str, *, thread_id: str | None = None) -> dict:
        """直连域图调用（不经主图适配器）。

        **thread_id 必须给**：TRAVEL_ENABLED=true 时域图带 checkpointer，
        LangGraph 会强制要求该键，缺失直接抛 ValueError —— 生产适配器
        ``travel_graph_node._build_invoke_config`` 也是必传的。

        且每个用例必须用**独立** thread_id：checkpointer 是进程级单例，
        所有用例共用 "t-e2e" 会让上一轮的行程状态泄漏到下一轮，
        测试结果随执行顺序漂移（实测：开启该开关后 9 个端到端用例集体失败）。
        """
        from uuid import uuid4

        tid = thread_id or f"t-e2e-{uuid4().hex[:8]}"
        return get_travel_graph().invoke(
            new_travel_graph_input(message, session_id="t-e2e"),
            config={"configurable": {"thread_id": tid}},
        )

    def test_full_plan_succeeds_and_passes_validation(self):
        final = self._invoke(
            "帮我规划福州3天行程，2个人，喜欢人文和摄影，"
            "一定要去三坊七巷和鼓山，预算3000元"
        )
        result = build_travel_graph_result(final)
        assert result["status"] == STATUS_SUCCESS
        errors = [v for v in final["validation"]["violations"] if v["level"] == "error"]
        assert errors == [], f"首版行程不应存在硬约束违反: {errors}"
        # 用 .get：域图输入不再预置默认值（预置会覆盖 checkpointer 的跨轮状态），
        # 所以「本轮没被写过的键」不会出现在最终状态里 —— 消费方一律 .get 兜底
        assert final.get("repair_rounds", 0) == 0

    def test_must_go_lands_in_itinerary(self):
        final = self._invoke(
            "福州3天，1个人，一定要去三坊七巷和鼓山")
        names = [i["title"] for d in final["itinerary"]["days"] for i in d["items"]]
        assert "三坊七巷" in names and "鼓山" in names

    def test_plan_is_deterministic(self):
        message = "厦门3天行程，2个人，想吃美食"
        first = self._invoke(message)
        second = self._invoke(message)
        # 确定性断言的是「规划产物」（天数/POI 序列/时刻/费用），不含生成
        # 时刻。时钟字段剥离（STOP J9 模式，Phase 4 补全）：
        # - created_at：行程生成时刻；
        # - legs[].observed_at：STOP J 时效语义给本地估算腿打的「此刻估算」
        #   观测戳（秒级，逐腿独立取时）——两轮间隔跨秒即假失败。实测触发：
        #   run1 的 risk 节点 RAG 初始化重试（known debt，knowledge 无显式
        #   超时）把轮间隔拉到 16s，观测戳必不同。同族时钟字段，同款剥离。
        a = dict(first["itinerary"] or {})
        b = dict(second["itinerary"] or {})
        for d in (a, b):
            d.pop("created_at", None)
            for day in d.get("days", []):
                for leg in day.get("legs", []):
                    leg.pop("observed_at", None)
        assert a == b

    def test_report_does_not_expose_unverified_seed_values(self):
        final = self._invoke("杭州2天行程，1个人")
        answer = build_travel_graph_result(final)["final_answer"]
        assert "示例数据" not in answer
        assert "seed:local" not in answer
        assert "费用预估" not in answer
        assert "置信度" not in answer
        assert "费用：暂无数据" in answer
        assert "坐标：暂无数据" in answer

    def test_clarification_when_destination_missing(self):
        final = self._invoke("我想出去旅游")
        result = build_travel_graph_result(final)
        assert result["status"] == STATUS_NEEDS_CLARIFICATION
        assert "去哪个城市" in result["final_answer"]
        assert final["step_count"] == 1  # 一步到 reporter，不做无用规划

    def test_unknown_city_asks_for_supported_destination(self):
        """不认识的地名不会硬编一份行程，而是回到追问并列出支持范围。"""
        final = self._invoke("帮我规划火星3天行程")
        result = build_travel_graph_result(final)
        assert result["status"] == STATUS_NEEDS_CLARIFICATION
        assert "去哪个城市" in result["final_answer"]
        assert "福州" in result["final_answer"]

    def test_empty_candidate_pool_is_reported_honestly(self, monkeypatch):
        """已支持的城市也可能取不到数据（P1 换 MCP 数据源后更常见）。

        此时必须如实说明「没有该城市的数据」，既不能崩，也不能凑一份空行程。
        """
        import backend.travel.experts.poi as poi_expert

        # Phase 3 补丁缝迁移：search_poi 已下沉 poi_service（节点级缝 =
        # retrieve_candidates），行为断言不变（候选池空 → 如实披露）
        monkeypatch.setattr(poi_expert, "retrieve_candidates",
                            lambda brief: ([], []))
        final = self._invoke("福州3天行程，1个人")
        result = build_travel_graph_result(final)
        assert result["status"] == STATUS_NO_DATA
        assert "暂时无法" in result["final_answer"]

    def test_relaxed_pace_yields_lighter_days(self):
        relaxed = self._invoke("福州2天，1个人，轻松一点")
        intense = self._invoke("福州2天，1个人，紧凑一点")
        relaxed_pois = sum(len(d["items"]) for d in relaxed["itinerary"]["days"])
        intense_pois = sum(len(d["items"]) for d in intense["itinerary"]["days"])
        assert relaxed_pois < intense_pois

    def test_every_visit_respects_opening_hours(self):
        """端到端最强断言：所有到访项都必须落在各自开放时段内。"""
        final = self._invoke("厦门4天怎么玩？3个人，想吃美食和拍照")
        for day in final["itinerary"]["days"]:
            for item in day["items"]:
                if item["kind"] != "visit":
                    continue
                poi = item["poi"]
                assert poi["open_time"] <= item["start"], (
                    f"{poi['name']} 于 {item['start']} 到访，早于开放 "
                    f"{poi['open_time']}")
                assert item["end"] <= poi["close_time"], (
                    f"{poi['name']} 于 {item['end']} 结束，晚于闭馆 "
                    f"{poi['close_time']}")


class TestRuntimeGuards:
    """护栏参数之间的数量关系 —— 配错就等于没有护栏。"""

    def test_recursion_limit_outlasts_step_guard(self):
        """recursion_limit 必须留够步数，否则 LangGraph 兜底抢在业务护栏之前抛错。

        一个调度回合 ≈ 2 个图步（supervisor + 它跳到的节点），故上限至少要有
        2×TRAVEL_MAX_STEPS；留不出余量时 step_count 永远到不了护栏线，
        用户看到的是 GraphRecursionError，而不是「已达步数上限，如实收尾」。
        """
        assert T.TRAVEL_GRAPH_RECURSION_LIMIT > 2 * T.TRAVEL_MAX_STEPS


# ==================== P2 城市解耦（2026-09-22） ====================

class TestTravelCityBoundary:
    """域识别与「城市是否支持」解耦：非种子城市的旅游请求先进域，
    由 slot_filler 的 unsupported-city 逻辑明确告知，禁止漏回 RAG 拒答。"""

    def test_unsupported_city_with_days_enters_travel(self):
        assert is_travel_request("帮我排纽约3天行程") is True

    def test_no_city_days_plus_travel_noun_enters_travel(self):
        """「帮我排3天行程」无城市：进域由 brief 追问目的地（D5 同路径）。"""
        assert is_travel_request("帮我排3天行程") is True

    def test_unsupported_city_without_days_not_travel(self):
        """单城市名无旅游名词/天数不判旅游（防误抢）。"""
        assert is_travel_request("纽约今天的新闻") is False

    def test_business_time_window_still_not_travel(self):
        assert is_travel_request("近3天的订单量") is False
        assert is_travel_request("统计本月订单金额") is False

    def test_unsupported_city_clarified_in_domain(self):
        """进域后：明确「暂不支持该城市」，不是知识库拒答。"""
        from backend.travel.models.brief import TravelBrief
        from backend.travel.slot_filler import (
            build_clarification,
            extract_unsupported_city,
        )

        msg = "帮我排纽约3天行程"
        assert extract_unsupported_city(msg) == "纽约"
        brief = TravelBrief(days=3)
        text = build_clarification(brief, msg)
        assert "纽约" in text
        assert "暂时无法规划" in text
        assert "福州" in text  # 给出可规划城市引导
