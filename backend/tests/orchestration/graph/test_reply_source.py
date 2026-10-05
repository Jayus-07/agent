"""reply_source 回复归因回归（2026-10-05 回复呈现规范）。

覆盖三层：
1. infer_reply_source 纯函数：capability 白名单推断、优先级、
   域图/email/失败步骤不标注；
2. make_done_event：缺省推断写入 done 帧、显式参数覆盖（拦截/澄清
   出口传 system_notice）、无可信依据字段缺省（前端无感）；
3. 帧契约：DoneData 校验带 reply_source 的 done 帧合法。
"""
import time

import pytest

from backend.orchestration.graph.events import (
    infer_reply_source,
    make_done_event,
)
from backend.orchestration.graph.event_schema import validate_frame


def _step(capability: str, status: str = "success") -> dict:
    return {"capability": capability, "status": status, "output": "x"}


# ── 1. 推断：白名单 ──────────────────────────────────────────

@pytest.mark.parametrize(("capability", "expected"), [
    ("rag.search", "knowledge_base"),
    ("sql.query", "data_analysis"),
    ("business.analyze", "data_analysis"),
    ("competitor.analyze", "data_analysis"),
    ("report.generate", "data_analysis"),
    ("map.lookup", "realtime_query"),
    ("web.search", "realtime_query"),
    ("travel.poi_search", "realtime_query"),
])
def test_whitelist_capability_maps_to_source(capability, expected):
    assert infer_reply_source({"1": _step(capability)}) == expected


# ── 2. 推断：不标注场景（None = 前端无徽章）──────────────────

def test_domain_graph_steps_without_capability_are_not_labeled():
    # 客服/旅游域图步骤不写 capability 字段 → 不标注
    assert infer_reply_source({"1": {"status": "success", "output": "行程"}}) is None


def test_email_and_workflow_names_are_not_labeled():
    # email.* 属内容复述、选品 workflow 蛇形名属漏斗域，均不在白名单
    assert infer_reply_source({"1": _step("email.read")}) is None
    assert infer_reply_source({"1": _step("market_research")}) is None


def test_failed_and_filtered_steps_are_ignored():
    # 失败步骤不构成归因依据（查不到 ≠ 基于数据分析回答）
    assert infer_reply_source({"1": _step("sql.query", status="error")}) is None


def test_empty_results_return_none():
    assert infer_reply_source({}) is None
    assert infer_reply_source({"1": "非 dict 步骤也安全"}) is None


# ── 3. 推断：多能力并存优先级 ────────────────────────────────

def test_rag_wins_over_data_and_realtime():
    steps = {"1": _step("sql.query"), "2": _step("rag.search"),
             "3": _step("map.lookup")}
    assert infer_reply_source(steps) == "knowledge_base"


def test_data_analysis_wins_over_realtime():
    steps = {"1": _step("map.lookup"), "2": _step("business.analyze")}
    assert infer_reply_source(steps) == "data_analysis"


# ── 4. make_done_event 组装 ─────────────────────────────────

def test_done_event_infers_from_step_results():
    evt = make_done_event("答案", {"1": _step("rag.search")},
                          start_time=time.time())
    assert evt["data"]["reply_source"] == "knowledge_base"
    validate_frame(evt)


def test_done_event_explicit_overrides_inference():
    # 拦截/澄清出口：step_results 为空 + 显式 system_notice
    evt = make_done_event("无法处理该问题。", {}, start_time=time.time(),
                          reply_source="system_notice")
    assert evt["data"]["reply_source"] == "system_notice"
    validate_frame(evt)


def test_done_event_without_evidence_omits_field():
    # 无可信依据 → 字段缺省（与 answer_status 同款「缺省=无感」口径）
    evt = make_done_event("闲聊回复", {}, start_time=time.time())
    assert "reply_source" not in evt["data"]
    validate_frame(evt)


def test_domain_graph_answer_omits_field():
    evt = make_done_event("行程如下…", {"1": {"status": "success", "output": "…"}},
                          start_time=time.time())
    assert "reply_source" not in evt["data"]
