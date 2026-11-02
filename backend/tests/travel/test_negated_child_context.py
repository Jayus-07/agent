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
