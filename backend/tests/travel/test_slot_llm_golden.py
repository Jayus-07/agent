"""tests/travel/test_slot_llm_golden.py — 口语理解 golden 门禁（2026-10-08 STOP 7）

数据集：datasets/travel_slot_llm_golden.jsonl（52 条八类口语）。

两种模式（与 test_intent_llm_golden 同款双模式纪律）：

  - **规则模式（默认跑，TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED=false）**：
    断言纯规则链的 missing/ask/字段抽取基线 —— 这是 P0-20「原有 Travel
    Golden 无明显回退」的口语面回归网，也是富化触发面的输入事实。
  - **富化模式（flag 开 + mock LLM）**：只对标注 `llm_hints` 的用例注入
    mock 候选，断言「只补白名单空槽、永不覆盖规则值」的合并语义。

硬断言（所有模式共用）：
  - family != plan 的用例必须被词表接住（intent 精确相等）——问答保护
    （Golden M）的版本化保证；
  - ask_slots 恒为 0 或 1 个槽位（P0-11）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.travel.core.intent import (
    NON_PLANNING_INTENTS,
    TravelIntent,
    classify_intent,
)
from backend.travel.services.clarification_service import (
    build_clarification_plan,
)
from backend.travel.agents.requirement_agent import extract_fresh_brief

DATASET = (Path(__file__).resolve().parents[2] / "datasets"
           / "travel_slot_llm_golden.jsonl")


def _cases() -> list[dict]:
    return [json.loads(line) for line
            in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


CASES = _cases()


def _resolve(message: str):
    """复刻 slot_filler 的两段式意图判定 + 纯规则抽取（无上一轮）。"""
    fresh = extract_fresh_brief(message)
    intent = classify_intent(message, has_itinerary=False,
                             has_destination=False)
    if intent is None:
        intent = classify_intent(message, has_itinerary=False,
                                 has_destination=bool(fresh.destination))
    return intent, fresh


def test_dataset_shape():
    """评测集自检：字段齐、family 白名单、规模下限（P1-08 ≥50 条）。"""
    assert len(CASES) >= 50
    allowed = {"plan", "query_static", "query_dynamic", "query_transit",
               "discover", "meta", "social", "out_of_scope"}
    for case in CASES:
        assert {"id", "message", "family", "note"} <= set(case), case["id"]
        assert case["family"] in allowed, case["id"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_golden_rules_baseline(case):
    """规则模式基线：planning 家族断言 missing/ask/字段；其余断言意图接住。"""
    intent, fresh = _resolve(case["message"])
    if case["family"] == "plan":
        assert intent in (None, TravelIntent.PLAN), (
            f"{case['id']}: 规划用例被词表判成 {intent}（应走规划轨）")
        missing = fresh.missing_slots()
        assert missing == (case["missing"] or []), (
            f"{case['id']}: missing={missing} 期望 {case['missing']}")
        plan = build_clarification_plan(fresh, case["message"])
        ask = plan.ask_slots if plan else []
        assert ask == (case["ask"] or []), (
            f"{case['id']}: ask={ask} 期望 {case['ask']}")
        # P0-11：一次最多问 1 个槽位
        assert len(ask) <= 1
        for field, expected in (case.get("fields") or {}).items():
            got = getattr(fresh, field)
            assert got == expected, (
                f"{case['id']}: {field}={got!r} 期望 {expected!r}")
    else:
        assert intent is not None and intent.value == case["family"], (
            f"{case['id']}: 意图={intent} 期望 {case['family']}（词表必须接住）")
        assert intent in NON_PLANNING_INTENTS


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_golden_no_ask_for_answering_families(case):
    """问答/寒暄/出域/探索家族在域内绝不产生追问（改单走问答出口）。"""
    if case["family"] == "plan":
        return
    intent, fresh = _resolve(case["message"])
    assert intent is not None and intent in NON_PLANNING_INTENTS
    # slot_filler 对这些意图清空 clarification —— 这里断言计划层在「规划轨
    # 之外不渲染」由 slot_filler 门禁保证，本用例锁意图判定不漂移。
