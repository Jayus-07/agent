"""tests/travel/test_transit_query_fastpath.py — QUERY_TRANSIT 车票查询快路

2026-10-08 #2：词表此前无交通词，「明天去厦门最快的车」被当成规划需求
追问「玩几天」（或把旧行程原样重吐）。快路契约：
  - 意图判定：交通名词/问法 → QUERY_TRANSIT，且 MODIFY/PLAN 优先级不变
  - 辅助任务层：查询车票（失败不阻塞）、缺出发地/目的地不猜、缺日期按明天兜底
  - supervisor：意图先行 REPORT 短路，不进专家链
  - graph_result：answered（itinerary=None、不写 pending）
  - prefilter：交通查询 + 城市名可冷启动进域；业务时间窗守卫不回退
"""
from __future__ import annotations

import pytest

from backend.orchestration.graph.travel_prefilter import is_travel_request
from backend.travel.core.intent import (
    NON_PLANNING_INTENTS,
    QUERY_INTENTS,
    TravelIntent,
    classify_intent,
)
from backend.travel.models.graph_result import (
    STATUS_ANSWERED,
    build_travel_graph_result,
)
from backend.travel.reporter import (
    _answer_transit_query,
    _assemble,
    _parse_duration_minutes,
)
from backend.travel.supervisor import TravelStage, decide


class TestIntentClassification:
    @pytest.mark.parametrize("message", [
        "明天去厦门的最快的车",
        "明天福州到厦门最快的高铁",
        "查一下福州到厦门的火车票",
        "高铁票价多少钱",
        "从厦门怎么去鼓浪屿",
        "今晚有没有动车去福州",
    ])
    def test_transit_queries_classified(self, message):
        assert classify_intent(message) is TravelIntent.QUERY_TRANSIT

    @pytest.mark.parametrize("message,itinerary", [
        # MODIFY 优先：「把…的火车换成…」是逐条改单不是查询
        ("把第二天的火车换成早上的一班", True),
        # PLAN 优先：主诉求是规划，高铁只是顺带
        ("帮我规划福州3天行程，顺便看下高铁", False),
        # 景区语义不误伤：门票/演出票归 QUERY_DYNAMIC
        ("鼓浪屿门票多少钱", False),
        ("云南印象演出票还有吗", False),
    ])
    def test_priority_not_regressed(self, message, itinerary):
        assert classify_intent(message, has_itinerary=itinerary) is not TravelIntent.QUERY_TRANSIT

    def test_membership(self):
        assert "query_transit" in QUERY_INTENTS
        assert "query_transit" in NON_PLANNING_INTENTS


class TestSupervisorShortCircuit:
    def test_query_transit_reports_without_experts(self):
        decision = decide({"intent": "query_transit", "brief_missing": ["days"]})
        assert decision.stage is TravelStage.REPORT
        assert "车票" in decision.reason or "直出" in decision.reason

    def test_query_with_pending_train_task_runs_auxiliary_node_first(self):
        decision = decide({
            "intent": "query_transit",
            "brief_missing": ["days"],
            "turn_decision": {"additional_tasks": [{
                "task_id": "train-1", "type": "query_train",
            }]},
            "task_results": [],
        })
        assert decision.stage is TravelStage.AUXILIARY_TASKS

    def test_completed_train_task_does_not_repeat(self):
        decision = decide({
            "intent": "query_transit",
            "brief_missing": ["days"],
            "turn_decision": {"additional_tasks": [{
                "task_id": "train-1", "type": "query_train",
            }]},
            "task_results": [{"task_id": "train-1", "status": "success"}],
        })
        assert decision.stage is TravelStage.REPORT


class TestGraphResultAnswered:
    def test_query_transit_is_answered(self):
        result = build_travel_graph_result({
            "intent": "query_transit",
            "final_answer": "车次…",
            "brief": {"destination": "厦门"},
            "brief_missing": ["days"],
        })
        assert result["status"] == STATUS_ANSWERED
        assert result["itinerary"] is None


class TestReporterRendering:
    def test_sorted_by_duration_with_disclosure(self):
        answer = _assemble({
            "intent": "query_transit",
            "transit_query": {
                "status": "ok", "origin": "福州", "destination": "厦门",
                "date": "2026-10-09", "source": "12306",
                "trains": [
                    {"train_no": "D6213", "start_time": "07:00",
                     "arrive_time": "09:10", "duration": "2小时10分",
                     "seats": {"二等座": "无"}},
                    {"train_no": "G510", "start_time": "08:10",
                     "arrive_time": "09:35", "duration": "1小时25分",
                     "seats": {"二等座": "有"}},
                ],
            },
        })
        assert answer.index("G510") < answer.index("D6213")
        assert "非官方聚合源" in answer
        assert "余票可能延迟" in answer

    def test_missing_origin(self):
        answer = _answer_transit_query({"transit_query": {
            "status": "missing_origin", "destination": "厦门"}})
        assert "从哪出发" in answer
        assert "福州" in answer  # 示例句可被下一轮直接解析

    def test_failed_honest(self):
        answer = _answer_transit_query({"transit_query": {
            "status": "failed", "destination": "厦门"}})
        assert "暂时不可用" in answer

    def test_no_direct_train(self):
        answer = _answer_transit_query({"transit_query": {
            "status": "ok", "origin": "福州", "destination": "厦门",
            "date": "2026-10-09", "trains": []}})
        assert "直达" in answer

    def test_duration_parse(self):
        assert _parse_duration_minutes("1小时25分") == 85
        assert _parse_duration_minutes("01:25") == 85
        assert _parse_duration_minutes("90分") == 90
        assert _parse_duration_minutes("") > 10 ** 5


class TestPrefilterColdStart:
    def test_transit_query_with_city_enters_travel(self):
        assert is_travel_request("明天去厦门的最快的车") is True
        assert is_travel_request("福州到厦门的高铁票还有吗") is True

    def test_business_window_guard_not_regressed(self):
        # 既有守卫不回退：业务时间窗/无城市的泛问不因新词表误入旅游域
        assert is_travel_request("近3天订单量") is False
        assert is_travel_request("怎么去黑眼圈") is False
