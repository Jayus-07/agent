from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.models.ai_edit import AITripEditCandidate
from backend.travel_v2.services.trip_ai_edit_service import (
    AIEditModelOutputError,
    TripAIEditService,
)
from backend.travel_v2.services.trip_edit_service import TripEditService
from backend.travel_v2.services.trip_write_validator import validate_trip_write


def _evening_document() -> dict:
    document = valid_document()
    item = dict(document["days"][0]["items"][0])
    item.update({
        "item_id": "item-evening", "title": "晚间活动",
        "start_time": "18:00", "duration_min": 60,
        "must_visit": False, "locked": False, "fixed_start": True,
        "place": None, "kind": "activity", "activity_type": "custom",
    })
    document["days"][0]["items"].append(item)
    document["days"][0]["legs"] = []
    return document


def test_ai_edit_candidate_schema_requires_one_typed_operation() -> None:
    candidate = AITripEditCandidate.model_validate({
        "outcome": "candidate", "change_summary": "调整西湖开始时间",
        "operation": {
            "op": "update_item", "item_id": "item-1", "start_time": "10:00",
        },
    })
    assert candidate.operation.op == "update_item"

    with pytest.raises(ValidationError):
        AITripEditCandidate.model_validate({
            "outcome": "candidate", "change_summary": "更新行程",
            "operation": {"op": "delete_trip", "trip_id": "trip-1"},
        })
    with pytest.raises(ValidationError):
        AITripEditCandidate.model_validate({
            "outcome": "candidate", "change_summary": "更新行程",
            "operation": {"op": "remove_item", "item_id": "item-1"},
            "extra": "must be rejected",
        })


def test_ai_edit_managed_prompt_renders_without_trip_data_variables() -> None:
    from backend.prompts.loader import load_defaults
    from backend.prompts.registry import PROMPT_REGISTRY
    from backend.prompts.renderer import PromptRenderer

    rendered = PromptRenderer.render(
        load_defaults()["travel.v2.trip_ai_edit"], {},
        spec=PROMPT_REGISTRY["travel.v2.trip_ai_edit"],
    )
    assert "不可信数据" in rendered
    assert "只输出一个 JSON 对象" in rendered
    assert "{user_message}" not in rendered


def test_trip_bound_intent_routes_query_modify_and_out_of_scope_safely() -> None:
    document = valid_document()
    assert TripAIEditService._classify("西湖好玩吗？", document) == "read_only"
    assert TripAIEditService._classify("把西湖开始时间改到10:00", document) == "modify"
    assert TripAIEditService._classify("把西湖停留时长改为180分钟", document) == "modify"
    assert TripAIEditService._classify("帮我写代码", document) == "unsupported"


def test_ai_edit_scope_requires_unique_real_target_and_explicit_value() -> None:
    document = valid_document()
    valid = {"op": "update_item", "item_id": "item-1", "start_time": "10:00"}
    TripAIEditService._validate_scope(
        "把西湖开始时间改到10:00", document, valid,
    )

    with pytest.raises(ValueError, match="目标不唯一|目标不属于"):
        TripAIEditService._validate_scope(
            "把不存在的地点开始时间改到10:00",
            document,
            {**valid, "item_id": "forged-item"},
        )
    with pytest.raises(ValueError, match="没有在用户请求中明确表达"):
        TripAIEditService._validate_scope(
            "把西湖安排晚一点",
            document,
            {**valid, "start_time": "15:00"},
        )

    duplicate_target = valid_document()
    duplicate = dict(duplicate_target["days"][0]["items"][0])
    duplicate.update({"item_id": "item-duplicate", "must_visit": False})
    duplicate_target["days"][0]["items"].append(duplicate)
    with pytest.raises(ValueError, match="目标不唯一"):
        TripAIEditService._validate_scope(
            "把西湖开始时间改到10:00", duplicate_target, valid,
        )


def test_ai_edit_rejects_multi_operation_scope_and_protected_item_removal() -> None:
    document = valid_document()
    with pytest.raises(ValueError, match="多个原子修改"):
        TripAIEditService._validate_scope(
            "把西湖删除，但再增加一个休息时间",
            document,
            {"op": "remove_item", "item_id": "item-1"},
        )

    with pytest.raises(ValueError, match="must_visit|必去"):
        TripAIEditService._validate_scope(
            "删除西湖", document,
            {"op": "remove_item", "item_id": "item-1"},
        )

    fixed_document = _evening_document()
    with pytest.raises(ValueError, match="fixed item cannot be removed"):
        TripEditService._apply_structured_operation(
            fixed_document,
            {"op": "remove_item", "item_id": "item-evening"},
        )

    fixed_document = _evening_document()
    changed_fixed = TripEditService._apply_structured_operation(
        fixed_document,
        {"op": "update_item", "item_id": "item-evening", "start_time": "19:00"},
    )
    with pytest.raises(ValueError, match="fixed item start time cannot change"):
        validate_trip_write(changed_fixed, current_document=fixed_document)

    locked_document = _evening_document()
    locked_document["days"][0]["items"][1]["locked"] = True
    with pytest.raises(ValueError, match="locked 行程项"):
        TripAIEditService._validate_scope(
            "删除晚间活动", locked_document,
            {"op": "remove_item", "item_id": "item-evening"},
        )


def test_ai_add_activity_inserts_at_requested_time_without_reordering_neighbors() -> None:
    document = _evening_document()
    operation = {
        "op": "add_activity", "day_id": "day-1",
        "activity_type": "meal", "title": "午餐",
        "start_time": "12:00", "duration_min": 60, "position": None,
    }
    TripAIEditService._validate_scope(
        "第1天午餐安排在12:00", document, operation,
    )
    TripAIEditService._place_add_activity_in_order(document, operation)
    changed = TripEditService._apply_structured_operation(document, operation)
    assert [item["title"] for item in changed["days"][0]["items"]] == [
        "西湖", "午餐", "晚间活动",
    ]
    assert changed["days"][0]["items"][-1]["fixed_start"] is True


def test_ai_candidate_call_keeps_request_data_in_untrusted_human_message(monkeypatch) -> None:
    from backend.config import model_roles
    from backend.infra import async_utils
    from backend.infra.llm import proxy

    captured = {}

    class FixtureLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(
                content=(
                    '{"outcome":"clarify",'
                    '"clarification":"请说明要修改哪一项。"}'
                ),
                usage_metadata={"input_tokens": 10, "output_tokens": 5},
            )

    monkeypatch.setattr(
        model_roles, "resolve_effective", lambda _role: {"value": "fixture-model"},
    )
    monkeypatch.setattr(
        proxy, "bind_llm_for_role", lambda *_args, **_kwargs: FixtureLLM(),
    )
    monkeypatch.setattr(
        async_utils, "sync_call_with_timeout",
        lambda function, _timeout: function(),
    )
    candidate, metadata = TripAIEditService._generate_candidate(
        "把西湖开始时间改到10:00",
        {"days": [{"day_id": "day-1", "items": []}]},
    )

    assert candidate.outcome == "clarify"
    assert "不可信数据" in captured["messages"][0].content
    assert "把西湖开始时间改到10:00" not in captured["messages"][0].content
    assert "把西湖开始时间改到10:00" in captured["messages"][1].content
    assert metadata["prompt_version"].startswith("travel.v2.trip_ai_edit@")
    assert metadata["input_tokens"] == 10
    assert metadata["output_tokens"] == 5


def test_ai_model_output_parser_rejects_invalid_json_and_unlisted_operation(monkeypatch) -> None:
    from backend.config import model_roles
    from backend.infra import async_utils
    from backend.infra.llm import proxy
    from backend.prompts.service import prompt_service

    monkeypatch.setattr(
        model_roles, "resolve_effective", lambda _role: {"value": "fixture-model"},
    )
    monkeypatch.setattr(
        prompt_service, "render_sync",
        lambda _key: SimpleNamespace(text="managed prompt", version=3),
    )
    monkeypatch.setattr(proxy, "bind_llm_for_role", lambda *_args, **_kwargs: object())

    for content in (
        "not json",
        '{"outcome":"candidate","change_summary":"x",'
        '"operation":{"op":"set_admin","id":"x"}}',
    ):
        response = SimpleNamespace(content=content, usage_metadata={})
        monkeypatch.setattr(
            async_utils, "sync_call_with_timeout",
            lambda func, _timeout: response,
        )
        with pytest.raises(AIEditModelOutputError):
            TripAIEditService._generate_candidate(
                "修改西湖时间", {"days": []},
            )
