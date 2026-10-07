"""tests/travel/test_tool_failure_policy.py — Tool 失败降级与容错契约回归

冻结契约（2026-10-07 任务书 §39）：
    Tool Failure ≠ Workflow Failure
    External Provider Failure ≠ Internal Planning Failure
    DEGRADED ≠ SUCCESS / FAILED；FAILED ≠ BLOCKED

覆盖任务书 §34 测试矩阵的核心条目：
  - test_tool_retry_success / retry_exhausted → executor 层已有
    （tests/tool_runtime），此处不重复 mock 重试计数
  - optional_tool_failure_degraded / required_tool_failure_blocked
  - 12306_failure_non_blocking_for_normal_plan
  - 12306_required_constraint_blocking
  - internal_planner_failure_blocked
  - degraded_plan_has_warning / degraded_plan_does_not_fabricate_data
  - tool_error_user_safe_message
  - query_social_no_full_replan_after_tool_policy（blocked 账不污染下一轮）
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services.live_search_service import LiveSearchError
from backend.travel.services import tool_failure_policy as tfp
from backend.travel.services.tool_failure_policy import (
    PROVIDER_12306,
    blocked_disclosure,
    hard_arrival_deadline,
    realtime_disclosure,
    resolve_transport_dependency,
    run_checked,
    state_records,
)


def _poi(poi_id: str = "p1", name: str = "鼓浪屿") -> Poi:
    return Poi(
        poi_id=poi_id, name=name, city="厦门", category="景点",
        lat=24.44, lng=118.06, suggested_minutes=120, ticket_cny=0.0,
        tags=[], rating=4.0, source="tencent:lbs", observed_at="2026-10-07T00:00:00+00:00",
        verification_status="unverified", reason="测试", location_status="verified",
    )


def _transit_state(user_message: str = "", origin: str = "福州") -> dict:
    return {
        "user_message": user_message,
        "brief": TravelBrief(
            destination="厦门", days=1, origin=origin,
            start_date=date(2026, 10, 8),
        ).model_dump(),
        "candidates": [_poi().model_dump()],
        "day_plan": [["p1"]],
    }


_HARD_MSG = "必须找到10月8日上午9点前抵达泉州的高铁，否则不要给我行程"


# =============================================
# 动态依赖判定（STOP G：不能静态配死 12306=optional）
# =============================================
class TestDependencyResolution:
    def test_hard_constraint_is_required(self):
        brief = TravelBrief(destination="泉州", origin="福州",
                            start_date=date(2026, 10, 8))
        assert resolve_transport_dependency(
            brief, _HARD_MSG) is tfp.ToolCriticality.REQUIRED

    def test_plain_origin_date_is_important(self):
        brief = TravelBrief(destination="泉州", origin="福州",
                            start_date=date(2026, 10, 8))
        assert resolve_transport_dependency(
            brief, "帮我做一个泉州3天游") is tfp.ToolCriticality.IMPORTANT

    def test_no_transport_request_is_optional(self):
        brief = TravelBrief(destination="泉州", days=2)
        assert resolve_transport_dependency(
            brief, "泉州玩两天") is tfp.ToolCriticality.OPTIONAL

    def test_arrival_time_without_terminal_stake_not_required(self):
        """「9点前到」只是到达时间事实（brief.arrival_time 承接），没有
        「否则不…」的终止语义不构成硬依赖。"""
        brief = TravelBrief(destination="泉州", origin="福州",
                            start_date=date(2026, 10, 8))
        assert resolve_transport_dependency(
            brief, "我上午9点前到泉州，帮我排一天"
        ) is tfp.ToolCriticality.IMPORTANT

    def test_deadline_parsed(self):
        assert hard_arrival_deadline(_HARD_MSG) == "09:00"
        assert hard_arrival_deadline("随便玩玩") == ""


# =============================================
# run_checked 契约
# =============================================
class TestRunChecked:
    def test_success_passthrough(self):
        value, result = run_checked(
            "t", "planning", lambda: {"trains": [1]},
            provider=PROVIDER_12306,
            dependency=tfp.ToolCriticality.IMPORTANT,
        )
        assert value == {"trains": [1]}
        assert result.status.value == "success"
        assert not result.blocking

    def test_optional_failure_degraded(self):
        value, result = run_checked(
            "t", "planning",
            lambda: (_ for _ in ()).throw(LiveSearchError("上游挂了")),
            provider=PROVIDER_12306,
            dependency=tfp.ToolCriticality.IMPORTANT,
        )
        assert value is None
        assert result.status is tfp.ToolStatus.DEGRADED
        assert not result.blocking
        assert "12306" in result.degraded_reason
        assert result.degraded_reason  # 用户可读原因

    def test_required_failure_blocking(self):
        value, result = run_checked(
            "t", "planning",
            lambda: (_ for _ in ()).throw(LiveSearchError("上游挂了")),
            provider=PROVIDER_12306,
            dependency=tfp.ToolCriticality.REQUIRED,
            constraint_hint="交通",
        )
        assert value is None
        assert result.blocking is True
        assert result.error_code == "hard_dependency_unverifiable"

    def test_internal_exception_propagates(self):
        """内部代码异常 ≠ 外部数据源失败：原样上抛，绝不静默降级。"""
        with pytest.raises(RuntimeError):
            run_checked(
                "t", "planning",
                lambda: (_ for _ in ()).throw(RuntimeError("排序越界")),
                provider=PROVIDER_12306,
                dependency=tfp.ToolCriticality.IMPORTANT,
            )

    def test_state_records_shapes(self):
        _, degraded = run_checked(
            "t", "planning",
            lambda: (_ for _ in ()).throw(LiveSearchError("x")),
            provider=PROVIDER_12306,
            dependency=tfp.ToolCriticality.IMPORTANT,
        )
        records = state_records("t", PROVIDER_12306, degraded, note="n")
        assert len(records["degraded_tools"]) == 1
        assert records["blocked_tools"] == []
        assert records["tool_failures"][0]["tool"] == "t"
        _, blocked = run_checked(
            "t", "planning",
            lambda: (_ for _ in ()).throw(LiveSearchError("x")),
            provider=PROVIDER_12306,
            dependency=tfp.ToolCriticality.REQUIRED,
        )
        records = state_records("t", PROVIDER_12306, blocked)
        assert records["degraded_tools"] == []
        assert len(records["blocked_tools"]) == 1


# =============================================
# transit 专家：12306 失败不再拖死整趟行程（STOP B）
# =============================================
class TestTransitTrainDegradation:
    def test_train_failure_degraded_itinerary_still_generated(self, monkeypatch):
        from backend.travel.experts import transit as transit_mod

        def _boom(*a, **kw):
            raise LiveSearchError("12306 上游不可用")

        monkeypatch.setattr(transit_mod._planning, "search_trains", _boom)
        update = transit_mod.transit_expert_node(_transit_state())

        # B1/B2：workflow 不 failed、存在 itinerary
        assert update["last_expert_result"]["status"] == "success"
        assert update.get("itinerary")
        # B3：不虚构实时车次（intercity 必须为空）
        it = update["itinerary"]
        assert it.get("intercity") in (None, [])
        # B4：用户收到实时交通未验证提示
        joined = "\n".join(update.get("notes", []))
        assert "12306" in joined and "未经验证" in joined
        # B5/B6/B7 的 state 账：degraded 记录在案、无阻断
        degraded = update.get("degraded_tools") or []
        assert any(d["tool"] == "travel_train_search_tool" for d in degraded)
        assert update.get("blocked_tools") in (None, [])
        assert update.get("tool_failures"), "原始失败账应记录"

    def test_train_failure_with_hard_constraint_blocks(self, monkeypatch):
        from backend.travel.experts import transit as transit_mod

        def _boom(*a, **kw):
            raise LiveSearchError("12306 上游不可用")

        monkeypatch.setattr(transit_mod._planning, "search_trains", _boom)
        update = transit_mod.transit_expert_node(
            _transit_state(user_message=_HARD_MSG))

        # STOP G：BLOCKED——无 itinerary（不产出假装满足约束的方案）
        assert update["last_expert_result"]["status"] == "failed"
        assert not update.get("itinerary")
        blocked = update.get("blocked_tools") or []
        assert any(b["tool"] == "travel_train_search_tool" for b in blocked)
        # 用户可读话术，不含异常细节
        assert "无法验证" in (blocked[0].get("user_message") or "")

    def test_internal_planner_failure_not_degraded(self, monkeypatch):
        """STOP H：内部排程算法异常 → 专家 failed（终停），不得 DEGRADED。"""
        from backend.travel.experts import transit as transit_mod

        monkeypatch.setattr(
            transit_mod._planning, "search_trains",
            lambda **kw: {"trains": [], "count": 0})

        def _boom(*a, **kw):
            raise RuntimeError("排程索引越界")

        monkeypatch.setattr(transit_mod._optimization, "build_itinerary", _boom)
        update = transit_mod.transit_expert_node(_transit_state())

        assert update["last_expert_result"]["status"] == "failed"
        assert not update.get("itinerary")
        # 内部失败不算降级账（External ≠ Internal）
        assert (update.get("degraded_tools") or []) == []
        assert (update.get("blocked_tools") or []) == []


# =============================================
# supervisor / reporter 终态呈现（STOP G/I 的服务端侧）
# =============================================
class TestSupervisorAndReporter:
    def test_supervisor_blocked_goes_report(self):
        from backend.travel.supervisor import TravelStage, decide

        state = {
            "blocked_tools": [{
                "tool": "travel_train_search_tool",
                "provider": PROVIDER_12306,
                "reason": "硬依赖无法验证：12306 实时班次暂时无法查询",
                "user_message": blocked_disclosure(PROVIDER_12306, "交通"),
            }],
            "candidates": [_poi().model_dump()],
            "step_count": 3,
        }
        decision = decide(state)
        assert decision.stage is TravelStage.REPORT
        assert "硬依赖" in decision.reason

    def test_supervisor_social_reply_not_blocked_by_stale_account(self):
        """上一轮 blocked（finished=true）后下一轮社交回复正常收尾，
        不被残留账本误终止（planning_reset 清账 + finished 先判双保险）。"""
        from backend.travel.supervisor import TravelStage, decide

        decision = decide({"finished": True, "blocked_tools": [{"tool": "x"}]})
        assert decision.stage is TravelStage.DONE

    def test_reporter_blocked_message_user_safe(self):
        from backend.travel.reporter import _assemble

        state = {
            "blocked_tools": [{
                "tool": "travel_train_search_tool",
                "provider": PROVIDER_12306,
                "reason": "硬依赖无法验证",
                "user_message": blocked_disclosure(PROVIDER_12306, "交通"),
            }],
            "brief": TravelBrief(destination="泉州").model_dump(),
            "candidates": [_poi().model_dump()],
        }
        answer = _assemble(state)
        assert "没有继续生成可能不成立的方案" in answer
        assert "RuntimeError" not in answer
        assert "asyncio" not in answer

    def test_reporter_degraded_plan_has_warning_section(self):
        """STOP I6/§16：行程生成 + 降级披露段（不是「全部已核实」）。"""
        from backend.tests.travel.conftest import (
            make_day,
            make_itinerary,
            make_poi,
        )
        from backend.travel.reporter import _assemble

        itinerary = make_itinerary(
            brief=TravelBrief(destination="厦门", days=1),
            days=[make_day(day_index=1)])
        state = {
            "brief": TravelBrief(destination="厦门", days=1).model_dump(),
            "candidates": [_poi().model_dump()],
            "itinerary": itinerary.model_dump(),
            "degraded_tools": [{
                "tool": "travel_train_search_tool",
                "provider": PROVIDER_12306,
                "note": realtime_disclosure(PROVIDER_12306),
            }],
        }
        answer = _assemble(state)
        assert "实时信息核验情况" in answer
        assert "未经验证" in answer

    def test_degraded_disclosure_does_not_fabricate_trains(self):
        """降级行程不得出现未经 Tool 返回的具体车次/票价文本。"""
        from backend.tests.travel.conftest import (
            make_day,
            make_itinerary,
        )
        from backend.travel.reporter import _assemble

        itinerary = make_itinerary(
            brief=TravelBrief(destination="厦门", days=1),
            days=[make_day(day_index=1)])
        state = {
            "brief": TravelBrief(destination="厦门", days=1).model_dump(),
            "candidates": [_poi().model_dump()],
            "itinerary": itinerary.model_dump(),
            "degraded_tools": [{
                "tool": "travel_train_search_tool",
                "provider": PROVIDER_12306,
                "note": realtime_disclosure(PROVIDER_12306),
            }],
        }
        answer = _assemble(state)
        # 披露允许说明「未验证」，不允许给出具体班次数据
        assert "G1234" not in answer
        assert "¥" not in answer.split("实时信息核验情况")[-1].split("##")[0]


# =============================================
# STOP D：增强型数据（知乎攻略）失败非阻断
# =============================================
class TestGuideFailureNonBlocking:
    def test_zhihu_failure_degrades_guides_only(self, monkeypatch):
        """攻略是增强信息：单路失败返回 error 标记，不抛出、不阻断。"""
        from backend.travel.agents import research_agent as ra_mod

        def _boom(**kw):
            raise LiveSearchError("知乎 MCP 不可用")

        monkeypatch.setattr(ra_mod.live_search_service,
                            "search_zhihu_guides", _boom)
        monkeypatch.setattr(
            ra_mod.live_search_service, "search_web_guides",
            lambda **kw: {"results": [{"title": "全网攻略"}]})

        guides = ra_mod.ResearchAgent().search_guides("泉州")
        assert guides["zhihu"].get("error")
        assert guides["web"]["results"][0]["title"] == "全网攻略"

    def test_all_guides_failure_still_error_envelope(self, monkeypatch):
        from backend.travel.agents import research_agent as ra_mod

        def _boom(**kw):
            raise LiveSearchError("知乎 MCP 不可用")

        monkeypatch.setattr(ra_mod.live_search_service,
                            "search_zhihu_guides", _boom)
        monkeypatch.setattr(ra_mod.live_search_service,
                            "search_web_guides", _boom)
        guides = ra_mod.ResearchAgent().search_guides("泉州")
        # 全失败也是 error 封套（交付端据此披露），绝不是伪造的空成功
        assert guides["zhihu"].get("error")
        assert guides["web"].get("error")


# =============================================
# 图级集成（STOP B/J API 证据）：故障注入驱动真实域图
# =============================================
class TestGraphIntegration:
    """经 get_travel_graph().invoke 走完整专家链，注入 12306 故障。

    断言三件事：①SSE 事件序（tool.started → tool.result status=degraded，
    无 run 级失败帧）；②行程照常产出且 intercity 不造假；③阻断场景下
    supervisor REPORT + reporter 用户可读话术 + 无行程（无 Active Plan）。
    """

    def _invoke(self, message: str, monkeypatch) -> tuple[dict, list[dict]]:
        from uuid import uuid4

        from backend.travel.core.events import travel_event_scope
        from backend.travel.graph_builder import get_travel_graph
        from backend.travel.graph_state import new_travel_graph_input

        def _boom(**kw):
            raise LiveSearchError("12306 上游不可用（注入）")

        from backend.travel.services import live_search_service

        monkeypatch.setattr(live_search_service, "search_trains", _boom)
        events: list[dict] = []
        tid = f"t-fp-{uuid4().hex[:8]}"
        with travel_event_scope(events.append):
            final = get_travel_graph().invoke(
                new_travel_graph_input(message, session_id="t-fp"),
                config={"configurable": {"thread_id": tid}},
            )
        return final, events

    def test_degraded_run_completes_with_itinerary(self, monkeypatch):
        from backend.travel.models.graph_result import (
            build_travel_graph_result,
        )

        final, events = self._invoke(
            "厦门1天行程，从福州出发，2026-10-08出发", monkeypatch)
        result = build_travel_graph_result(final)

        # B1/B2：workflow 不 FAILED、行程存在
        assert result["status"] == "success"
        assert final.get("itinerary")
        # B3：不虚构实时车次
        assert (final["itinerary"].get("intercity") or []) == []
        # B5/B7：SSE 事件序里有 degraded 标记，且没有 blocked / run 失败
        tool_results = [e for e in events if e.get("event") == "tool.result"
                        and e.get("tool") == "travel_train_search_tool"]
        assert tool_results, "缺少 travel_train_search_tool 事件"
        assert tool_results[-1]["status"] == "degraded"
        assert "user_message" in tool_results[-1]
        assert not [e for e in events if e.get("event") == "tool.blocked"]
        # B4：降级账入 state，note 带披露
        degraded = final.get("degraded_tools") or []
        assert any(d["tool"] == "travel_train_search_tool" for d in degraded)
        # 行程单里有「实时信息核验情况」披露
        assert "实时信息核验情况" in (result.get("final_answer") or "")

    def test_blocked_run_reports_without_itinerary(self, monkeypatch):
        from backend.travel.models.graph_result import (
            build_travel_graph_result,
        )

        # 消息不含「高铁/车票」等交通词（2026-10-08 修 a2344db 先行漂移）：
        # QUERY_TRANSIT 快路会把含交通词的消息接走出问答出口，blocked 契约
        # 根本不进规划链；硬依赖阻断测试需要一条纯规划消息。
        final, events = self._invoke(
            "必须找到2026-10-08上午9点前抵达厦门的车，否则不要给我行程，"
            "从福州出发，厦门1天", monkeypatch)
        result = build_travel_graph_result(final)

        # STOP G：BLOCKED——无行程、无版本账本记录（K10）
        assert not final.get("itinerary")
        assert result["status"] == "failed"
        # 事件序：blocked 标记出现，且随后 supervisor 收尾
        blocked_events = [e for e in events if e.get("event") == "tool.blocked"]
        assert blocked_events, "缺少 tool.blocked 事件"
        tool_results = [e for e in events if e.get("event") == "tool.result"
                        and e.get("tool") == "travel_train_search_tool"]
        assert tool_results[-1]["status"] == "blocked"
        # reporter 用户可读话术（无内部异常细节）
        answer = result.get("final_answer") or ""
        assert "没有继续生成可能不成立的方案" in answer
        assert "RuntimeError" not in answer and "Traceback" not in answer
class TestTraceSemantics:
    def test_degraded_projection(self):
        from backend.travel.trace_semantics import build_trace_semantics

        semantics = build_trace_semantics({
            "intent": "",
            "degraded_tools": [{"tool": "travel_train_search_tool"}],
            "blocked_tools": [],
        })
        assert semantics["degraded_tools"] == ["travel_train_search_tool"]
        assert semantics["workflow_continued"] is True

    def test_blocked_projection(self):
        from backend.travel.trace_semantics import build_trace_semantics

        semantics = build_trace_semantics({
            "intent": "",
            "degraded_tools": [],
            "blocked_tools": [{"tool": "travel_train_search_tool"}],
        })
        assert semantics["workflow_continued"] is False
        assert semantics["blocked_tools"] == ["travel_train_search_tool"]
