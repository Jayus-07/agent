"""STOP2 偏好与一次性 Trip 状态隔离验收。"""
from __future__ import annotations

from backend.config import travel as travel_config
from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.slot_filler import slot_filler_node


def test_trip_preferences_do_not_cross_session(monkeypatch):
    monkeypatch.setattr(travel_config, "TRAVEL_PREFS_ENABLED", True)
    writes = []
    monkeypatch.setattr(
        "backend.tools.travel.preferences.get_preferences",
        lambda _user_id: {},
    )
    monkeypatch.setattr(
        "backend.tools.travel.preferences.upsert_preferences",
        lambda user_id, **fields: writes.append((user_id, fields)) or True,
    )

    session_a = slot_filler_node({
        "user_id": "stop2-user",
        "user_message": "我从上海去杭州玩三天，喜欢美食，节奏轻松",
    })
    session_b = slot_filler_node({
        "user_id": "stop2-user",
        "user_message": "我想去苏州玩三天",
    })

    assert session_a["brief"]["origin"] == "上海"
    assert session_a["brief"]["destination"] == "杭州"
    assert session_b["brief"]["origin"] == ""
    assert session_b["brief"]["destination"] == "苏州"
    assert session_b["brief"]["pace"] == "moderate"
    assert session_b["brief"]["preferences"] == []
    assert session_b["brief"]["budget_cny"] is None
    assert session_b["brief"]["must_go"] == []
    assert session_b["brief"]["avoid"] == []
    assert writes == []


def test_only_explicit_long_term_preference_is_persisted_and_inherited(monkeypatch):
    monkeypatch.setattr(travel_config, "TRAVEL_PREFS_ENABLED", True)
    stored = {}

    def fake_get(_user_id):
        return dict(stored)

    def fake_upsert(_user_id, **fields):
        stored.update({k: v for k, v in fields.items() if v})
        return True

    monkeypatch.setattr("backend.tools.travel.preferences.get_preferences", fake_get)
    monkeypatch.setattr("backend.tools.travel.preferences.upsert_preferences", fake_upsert)

    remembered = slot_filler_node({
        "user_id": "stop2-user",
        "user_message": "记住我以后旅行喜欢慢节奏",
    })
    assert remembered["intent"] == "meta"
    assert remembered.get("brief", {}).get("destination", "") == ""
    assert stored["pace"] == "relaxed"
    assert remembered["slot_sources"]["long_term_preference"] == "explicit"

    stored["preferences"] = ["人文"]
    next_session = slot_filler_node({
        "user_id": "stop2-user",
        "user_message": "我想去苏州玩三天",
    })
    assert next_session["brief"]["destination"] == "苏州"
    assert next_session["brief"]["preferences"] == ["人文"]
    assert next_session["brief"]["pace"] == "relaxed"


def test_long_term_preference_never_persists_trip_fields(monkeypatch):
    monkeypatch.setattr(travel_config, "TRAVEL_PREFS_ENABLED", True)
    writes = []
    monkeypatch.setattr(
        "backend.tools.travel.preferences.get_preferences", lambda _user_id: {}
    )
    monkeypatch.setattr(
        "backend.tools.travel.preferences.upsert_preferences",
        lambda _user_id, **fields: writes.append(fields) or True,
    )
    slot_filler_node({
        "user_id": "stop2-user",
        "user_message": "以后旅行我一般喜欢历史文化，预算3000元，去杭州三天",
    })
    assert writes
    assert set(writes[0]) <= {"preferences", "pace", "diet"}
    assert "预算" not in str(writes[0])
    assert "杭州" not in str(writes[0])
