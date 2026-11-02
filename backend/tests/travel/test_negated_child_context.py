"""明确不携儿童时，不得从否定词面派生亲子、同伴或轻松节奏。"""
from __future__ import annotations

import pytest

from backend.travel.agents.requirement_agent import (
    extract_fresh_brief,
    extract_group_pace,
    extract_party_size,
    extract_preferences,
    party_size_source,
)


@pytest.mark.parametrize(
    "context", ["无儿童", "不带孩子", "没有小孩", "不带娃", "0个儿童"]
)
def test_negative_child_context_does_not_create_family_preferences(context):
    message = f"苏州两日游，喜欢历史和自然，{context}"
    brief = extract_fresh_brief(message)
    assert brief.preferences == ["自然", "人文"]
    assert brief.pace == "moderate"
    assert brief.party_size == 1
    assert extract_group_pace(message) is None
    assert extract_party_size(message) is None
    assert party_size_source(message) == "none"


@pytest.mark.parametrize(
    "context", ["带孩子", "带娃", "亲子游", "2个儿童", "10个儿童", "20个儿童"]
)
def test_positive_child_context_still_sets_family_preferences(context):
    assert "亲子" in extract_preferences(context)
    assert extract_group_pace(context) == "relaxed"


def test_negative_child_context_preserves_other_positive_signals():
    brief = extract_fresh_brief("苏州两日游，2位成人，不带孩子，带爸妈，慢慢玩")
    assert "亲子" not in brief.preferences
    assert brief.pace == "relaxed"
    assert brief.adults == 2
    assert extract_party_size("不带孩子，带爸妈去苏州") == 3


def test_independent_positive_family_preference_is_not_removed():
    assert "亲子" in extract_preferences("不带孩子，但想考察亲子酒店")


# ── Q2 审计修复回归：连接词枚举扩充（不想/不要带孩子）────────────────
# 历史缺陷：「不(带|携带|需要|考虑|安排|想要)?」缺「想/要」单独形式，
# 「不想带孩子」「不要带孩子」不被屏蔽，残留「带孩子」被 _RE_COMPANION
# 误判为同伴 guess（苏州两日游被推出 2 人 + 亲子节奏）。


@pytest.mark.parametrize(
    "context",
    [
        "不想带孩子", "不要带孩子", "不想带娃", "不打算带孩子",
        "不需要亲子游", "别带孩子", "没有带小孩", "不准备带娃",
    ],
)
def test_extended_negated_phrases_do_not_create_companion_or_family(context):
    message = f"苏州两日游，喜欢历史和自然，{context}"
    assert extract_party_size(message) is None
    assert party_size_source(message) == "none"
    assert "亲子" not in extract_preferences(message)
    assert extract_group_pace(message) is None


def test_negation_then_transition_keeps_positive_preference():
    """否定在前、转折正向在后：人数不猜（Brief 回落默认 1 人），亲子偏好
    仍从转折句提取。"""
    brief = extract_fresh_brief("苏州两日游，不要带孩子，但想考察亲子酒店")
    assert brief.party_size == 1
    assert "亲子" in brief.preferences
    assert extract_party_size("苏州两日游，不想带孩子，但想考察亲子酒店") is None
    assert "亲子" in extract_preferences("不想带孩子，但想看看亲子酒店")


def test_positive_child_word_after_unrelated_negation_still_counts():
    """句中有「不」但与儿童词之间隔着非连接词内容时不得误吞
    （刻意不做自由字符桥接）。"""
    assert extract_party_size("不妨带孩子去乐园") == 2
    assert extract_group_pace("不妨带孩子去乐园") == "relaxed"
