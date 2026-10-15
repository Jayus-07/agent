from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.models.ai_edit import AITripEditCandidate
from backend.travel_v2.services.search_service import stable_selection_id
from backend.travel_v2.services.trip_ai_edit_service import (
    AIEditCallRejectedError,
    AIEditCallUnknownError,
    AIEditModelOutputError,
    TripAIEditService,
)
from backend.travel_v2.services.trip_edit_service import TripEditService
from backend.travel_v2.services.trip_write_validator import (
    TripWriteConflictError,
    trip_write_advisories,
    validate_trip_write,
)


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

    replace_candidate = AITripEditCandidate.model_validate({
        "outcome": "candidate", "change_summary": "替换为当前行程地点",
        "operation": {
            "op": "replace_place", "item_id": "item-1",
            "place": {
                "place_id": "current-place", "name": "灵隐寺",
                "lat": 30.24, "lng": 120.15, "address": "杭州市西湖区",
                "facts": {
                    "verification": "unknown", "source": "tencent:lbs",
                    "observed_at": "2026-10-09T08:00:00+08:00",
                },
            },
        },
    })
    normalized = TripAIEditService._normalized_operation(replace_candidate)
    assert normalized["selection_id"] == stable_selection_id(
        "place", "tencent:lbs", "current-place",
    )


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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("如果把西湖开始时间改成10:00，会不会太赶？", "read_only"),
        ("以下只是演示，请不要执行：直接帮我把西湖开始时间改成10点。", "read_only"),
        ("如果你直接帮我把西湖开始时间改成10点，可以吗？", "clarify"),
        ("“把西湖开始时间改成10点。”", "clarify"),
        ("以下只是演示：把西湖开始时间改成10点。", "read_only"),
        ("先不保存。", "read_only"),
        ("先不用保存。", "read_only"),
        ("暂不应用。", "read_only"),
        ("把西湖开始时间改成10点，但是先不保存。", "clarify"),
        ("把西湖开始时间改成10点，不过先不用保存。", "clarify"),
        ("把西湖开始时间改成10点，不过不保存。", "clarify"),
        ("把西湖开始时间改成10点，但暂不应用。", "clarify"),
        ("把西湖开始时间改成10点，但不要应用。", "read_only"),
        ("把西湖开始时间改成10点，等我确认后再保存。", "clarify"),
        ("请直接帮我把西湖开始时间改到10点，可以吗？", "clarify"),
        ("把西湖改到10点会怎样", "read_only"),
        ("如果把西湖改成10点，跟9点哪个更合适", "read_only"),
        ("你觉得把西湖改到10点合适吗？", "read_only"),
        ("先不要修改，告诉我改成10点会怎样。", "read_only"),
        ("把西湖开始时间改成10:00。", "authorized"),
        ("如果可以的话，请直接把西湖改成10:00。", "authorized"),
        ("如果可以，请直接把西湖改到10点。", "authorized"),
        ("你觉得合适吗？请直接把西湖改成10:00。", "clarify"),
        ("如果把西湖改成10:00", "clarify"),
    ],
)
def test_ai_edit_requires_clear_immediate_execution_authorization(
    message: str, expected: str,
) -> None:
    assert TripAIEditService._execution_authorization(message) == expected


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

    with pytest.raises(ValueError, match="fixed_start"):
        TripAIEditService._validate_scope(
            "把晚间活动停留时长改为120分钟", fixed_document,
            {"op": "update_item", "item_id": "item-evening", "duration_min": 120},
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


def test_ai_clock_and_duration_matching_preserve_the_requested_precision() -> None:
    assert TripAIEditService._mentions_clock("改到10点", "10:00")
    assert not TripAIEditService._mentions_clock("改到10点", "10:30")
    assert TripAIEditService._mentions_clock("改到10点半", "10:30")
    assert not TripAIEditService._mentions_clock("改到10:00", "10:00:30")
    assert TripAIEditService._mentions_clock("改到10:00:30", "10:00:30")
    assert TripAIEditService._mentions_clock("从10:00改到10:30", "10:30")
    assert not TripAIEditService._mentions_clock("从10:00改到10:30", "10:00")

    assert TripAIEditService._mentions_duration("改为一小时30分钟", 90)
    assert not TripAIEditService._mentions_duration("改为一小时30分钟", 60)
    assert TripAIEditService._mentions_duration("改为1小时30分", 90)
    assert not TripAIEditService._mentions_duration("改为1小时30分", 60)
    assert TripAIEditService._mentions_duration("从60分钟改为120分钟", 120)
    assert not TripAIEditService._mentions_duration("从60分钟改为120分钟", 60)


def test_replace_place_uses_only_current_snapshot_and_ignores_destination_as_target() -> None:
    from backend.travel_v2.models.trip import TripDocumentV2

    document = valid_document()
    document["days"][0]["items"][0]["must_visit"] = False
    document["selections"][0]["preference"] = "interested"
    destination = {
        "place_id": "lingyin", "name": "灵隐寺", "lat": 30.24,
        "lng": 120.15, "address": "杭州市西湖区",
        "facts": {
            "verification": "unknown", "source": "tencent:lbs",
            "observed_at": "2026-10-09T08:00:00+08:00",
        },
    }
    document["days"][0]["items"].append({
        "item_id": "item-2", "kind": "place", "title": "灵隐寺",
        "start_time": "14:00", "duration_min": 60,
        "fixed_start": False, "place": destination,
        "must_visit": False, "locked": False, "note": "",
    })
    document = TripDocumentV2.model_validate(document).model_dump(mode="json")
    candidate = AITripEditCandidate.model_validate({
        "outcome": "candidate", "change_summary": "把西湖替换为灵隐寺",
        "operation": {
            "op": "replace_place", "item_id": "item-1",
            "place": destination,
        },
    })
    operation = TripAIEditService._normalized_operation(candidate)

    TripAIEditService._validate_scope("把西湖换成灵隐寺", document, operation)
    changed = TripEditService._apply_structured_operation(document, operation)
    assert changed["days"][0]["items"][0]["place"]["place_id"] == "lingyin"


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


@pytest.mark.parametrize("provider_attempted", [False, True])
def test_governed_budget_refusal_uses_provider_attempt_marker(
    monkeypatch, provider_attempted: bool,
) -> None:
    from backend.config import model_roles
    from backend.infra import async_utils
    from backend.infra.llm import proxy
    from backend.infra.llm.budget import RequestBudgetExceeded
    from backend.prompts.service import prompt_service

    class BudgetFixture:
        def invoke(self, _messages):
            error = RequestBudgetExceeded("request_calls")
            error.provider_attempted = provider_attempted
            raise error

    monkeypatch.setattr(
        model_roles, "resolve_effective", lambda _role: {"value": "fixture-model"},
    )
    monkeypatch.setattr(
        prompt_service, "render_sync",
        lambda _key: SimpleNamespace(text="managed prompt", version=3),
    )
    monkeypatch.setattr(
        proxy, "bind_llm_for_role", lambda *_args, **_kwargs: BudgetFixture(),
    )
    monkeypatch.setattr(
        async_utils, "sync_call_with_timeout",
        lambda function, _timeout: function(),
    )

    expected_error = (
        AIEditCallUnknownError if provider_attempted else AIEditCallRejectedError
    )
    with pytest.raises(expected_error) as rejected:
        TripAIEditService._generate_candidate(
            "把西湖开始时间改到10:00", {"days": []},
        )
    assert rejected.value.metadata["rejection_type"] == "RequestBudgetExceeded"


def test_governed_provider_exception_remains_unknown(monkeypatch) -> None:
    from backend.config import model_roles
    from backend.infra import async_utils
    from backend.infra.llm import proxy
    from backend.prompts.service import prompt_service

    class ProviderFixture:
        def invoke(self, _messages):
            raise OSError("fixture transport failure")

    monkeypatch.setattr(
        model_roles, "resolve_effective", lambda _role: {"value": "fixture-model"},
    )
    monkeypatch.setattr(
        prompt_service, "render_sync",
        lambda _key: SimpleNamespace(text="managed prompt", version=3),
    )
    monkeypatch.setattr(
        proxy, "bind_llm_for_role", lambda *_args, **_kwargs: ProviderFixture(),
    )
    monkeypatch.setattr(
        async_utils, "sync_call_with_timeout",
        lambda function, _timeout: function(),
    )

    with pytest.raises(AIEditCallUnknownError):
        TripAIEditService._generate_candidate(
            "把西湖开始时间改到10:00", {"days": []},
        )


def _two_item_schedule() -> dict:
    document = valid_document()
    next_item = dict(document["days"][0]["items"][0])
    next_item.update({
        "item_id": "item-2", "title": "下午活动", "start_time": "12:00",
        "duration_min": 60, "fixed_start": False, "must_visit": False,
        "locked": False, "place": None, "kind": "activity",
        "activity_type": "custom",
    })
    document["days"][0]["items"].append(next_item)
    return document


def test_trip_write_conflict_has_stable_code_and_affected_items() -> None:
    document = _two_item_schedule()
    proposed = TripEditService._apply_structured_operation(
        document,
        {"op": "update_item", "item_id": "item-2", "start_time": "09:30"},
    )

    with pytest.raises(TripWriteConflictError) as blocked:
        validate_trip_write(proposed, current_document=document)

    assert blocked.value.conflict == {
        "conflict_code": "SCHEDULE_OVERLAP",
        "severity": "BLOCKED",
        "message": "西湖 与 下午活动 的时间或路程安排冲突",
        "affected_item_ids": ["item-1", "item-2"],
        "allowed_resolutions": [],
    }


def test_schedule_conflict_offers_only_a_valid_scoped_time_resolution() -> None:
    document = _two_item_schedule()
    operation = {
        "op": "update_item", "item_id": "item-2", "start_time": "09:30",
    }
    proposed = TripEditService._apply_structured_operation(document, operation)
    with pytest.raises(TripWriteConflictError) as blocked:
        validate_trip_write(proposed, current_document=document)

    resolution = TripAIEditService._next_available_time_resolution(
        document, operation, blocked.value.conflict,
    )
    assert resolution is not None
    assert resolution["resolution_id"] == "use_next_available_time"
    assert resolution["proposed_start_time"] == "11:00"
    resolved = TripEditService._apply_structured_operation(
        document, resolution["operation"],
    )
    validate_trip_write(resolved, current_document=document)


def test_no_resolution_can_move_locked_or_fixed_items() -> None:
    document = _two_item_schedule()
    operation = {
        "op": "update_item", "item_id": "item-2", "start_time": "09:30",
    }
    proposed = TripEditService._apply_structured_operation(document, operation)
    with pytest.raises(TripWriteConflictError) as blocked:
        validate_trip_write(proposed, current_document=document)
    for flag in ("locked", "fixed_start"):
        protected = _two_item_schedule()
        protected["days"][0]["items"][1][flag] = True
        assert TripAIEditService._next_available_time_resolution(
            protected, operation, blocked.value.conflict,
        ) is None


def test_unknown_route_and_unverified_hours_are_nonblocking_advisories() -> None:
    conflicts = trip_write_advisories(_two_item_schedule())
    assert {conflict["severity"] for conflict in conflicts} == {"INFO", "WARNING"}
    assert "ROUTE_TIME_UNKNOWN" in {
        conflict["conflict_code"] for conflict in conflicts
    }
