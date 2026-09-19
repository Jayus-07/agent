"""test_initial_state_normalize.py — P1 下游收敛：state["question"] 用规范化文本。

规划稿 §五：原文只用于审计和展示；路由、检索、实体提取使用规范化文本。
make_initial_state 以 guard_result.normalized_query 优先，缺失回退原文。
"""
from backend.orchestration.graph.events import make_initial_state


class TestInitialStateNormalization:

    def test_normalized_query_preferred(self):
        raw = "查订单　ＤＥＭＯ－１００６"  # 全角空格 + 全角字符
        state = make_initial_state(
            raw, "s1", "default", [],
            guard_result={"normalized_query": "查订单 DEMO-1006"},
        )
        assert state["question"] == "查订单 DEMO-1006"

    def test_fallback_to_raw_without_guard(self):
        state = make_initial_state("我的订单怎么申请退款  ", "s1", "default", [])
        assert state["question"] == "我的订单怎么申请退款"

    def test_fallback_when_normalized_empty(self):
        state = make_initial_state("查一下订单", "s1", "default", [],
                                   guard_result={"normalized_query": ""})
        assert state["question"] == "查一下订单"

    def test_guard_result_still_carried(self):
        state = make_initial_state("q", "s1", "default", [],
                                   guard_result={"normalized_query": "q",
                                                 "action": "allow"})
        assert state["guard_result"]["action"] == "allow"
