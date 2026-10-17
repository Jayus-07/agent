from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from pydantic import ValidationError

from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.postgres_test_support import assert_isolated_travel_postgres
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.models.commands import StructuredTripEditCommand
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.repositories.revision_repository import RevisionRepository
from backend.travel_v2.repositories.trip_repository import TripRepository
from backend.travel_v2.services.search_service import stable_selection_id
from backend.travel_v2.services.trip_edit_service import (
    IdempotencyConflict,
    TripEditService,
    VersionConflict,
)
from backend.travel_v2.services.trip_service import (
    IdempotencyConflict as TripCreateIdempotencyConflict,
)
from backend.travel_v2.services.trip_service import (
    TripNotFound,
    TripService,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires an explicitly selected isolated travel V2 PostgreSQL",
)


@pytest.fixture(autouse=True)
def _isolated_postgres_guard() -> None:
    assert_isolated_travel_postgres()


def _cleanup_trip(trip_id: str) -> None:
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM travel_v2.edit_operations WHERE trip_id = %s::uuid",
                (trip_id,),
            )
            cursor.execute(
                "DELETE FROM travel_v2.trip_revisions WHERE trip_id = %s::uuid",
                (trip_id,),
            )
            cursor.execute(
                "DELETE FROM travel_v2.trips WHERE trip_id = %s::uuid",
                (trip_id,),
            )
        connection.commit()
    finally:
        connection.close()


def _operation_keys(trip_id: str) -> set[str]:
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT idempotency_key FROM travel_v2.edit_operations "
                "WHERE trip_id = %s::uuid",
                (trip_id,),
            )
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


def _two_item_document(second_start_time: str = "13:00") -> dict:
    document = valid_document()
    document["days"][0]["items"].append({
        "item_id": "item-2", "kind": "activity", "activity_type": "rest",
        "title": "湖边休息", "start_time": second_start_time,
        "duration_min": 30, "fixed_start": False, "place": None,
        "must_visit": False, "locked": False, "note": "",
    })
    return document


def _lodging_command(expected_revision: int, *, check_out: str) -> dict:
    merchant_id = "hotel-selection-1"
    return {
        "expected_revision": expected_revision,
        "lodging": {
            "selection_id": stable_selection_id("hotel", "amap", merchant_id),
            "merchant": {
                "merchant_id": merchant_id, "name": "西湖酒店", "source": "amap",
                "observed_at": "2026-10-19T08:00:00+08:00",
            },
            "check_in": "2026-10-20", "check_out": check_out,
            "source": "amap", "queried_at": "2026-10-19T08:00:00+08:00",
            "verification": "unknown", "booking_status": "not_booked",
            "nightly_price_cny": None,
        },
    }


def _train_command(
    expected_revision: int, *, departure_time: str, arrival_time: str,
) -> dict:
    travel_date = "2026-10-20"
    departure_station = "上海虹桥"
    arrival_station = "杭州东"
    train_code = "G1"
    identity = "|".join((
        travel_date, departure_station, arrival_station, train_code,
    ))
    return {
        "expected_revision": expected_revision,
        "train": {
            "selection_id": stable_selection_id("train", "12306", identity),
            "train_code": train_code, "travel_date": travel_date,
            "departure_station": departure_station,
            "arrival_station": arrival_station,
            "departure_time": departure_time, "arrival_time": arrival_time,
            "duration_min": 60, "tickets": {"二等座": "有"},
            "fares": {"二等座": 73}, "ticket_source": "12306",
            "fare_source": "12306", "queried_at": "2026-10-19T08:00:00+08:00",
            "verification": "verified", "booking_status": "not_booked",
        },
    }


@pytest.fixture
def trip_scope():
    assert_isolated_travel_postgres()
    scope = {
        "tenant_id": f"test-{uuid.uuid4().hex[:12]}",
        "owner_id": f"user-{uuid.uuid4().hex[:12]}",
        "title": "杭州周末",
    }
    create_key = f"create-{uuid.uuid4().hex}"
    trip = TripService().create_trip(
        tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
        title=scope["title"],
        actor_id=scope["owner_id"],
        document=TripDocumentV2.model_validate(valid_document()),
        idempotency_key=create_key,
    )
    scope.pop("title")
    scope["trip_id"] = trip["trip_id"]
    try:
        yield scope, trip
    finally:
        _cleanup_trip(scope["trip_id"])


def test_trip_creation_writes_initial_revision_and_is_tenant_scoped(trip_scope) -> None:
    scope, created = trip_scope
    service = TripService()

    assert created["revision"] == 1
    assert service.get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )["document"]["schema_version"] == 2
    assert service.list_trips(
        tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )[0]["trip_id"] == scope["trip_id"]
    with pytest.raises(TripNotFound):
        service.get_trip(
            scope["trip_id"], tenant_id="another-tenant", owner_id=scope["owner_id"]
        )
    with pytest.raises(TripNotFound):
        service.get_trip(
            scope["trip_id"], tenant_id=scope["tenant_id"], owner_id="another-user"
        )


def test_trip_conversation_is_write_once_and_owner_scoped() -> None:
    tenant_id = f"test-{uuid.uuid4().hex[:12]}"
    owner_id = f"user-{uuid.uuid4().hex[:12]}"
    create_key = f"create-{uuid.uuid4().hex}"
    conversation_id = f"travel-{uuid.uuid4().hex}"
    service = TripService()
    created = service.create_trip(
        tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
        idempotency_key=create_key, title="杭州周末",
        document=TripDocumentV2.model_validate(valid_document()),
        conversation_id=conversation_id,
    )
    trip_id = created["trip_id"]
    try:
        conversation = service.get_trip_conversation(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )

        assert created["conversation_id"] == conversation["conversation_id"]
        assert conversation["linked"] is True
        assert datetime.fromisoformat(conversation["server_now"]).tzinfo is not None
        replay = service.create_trip(
            tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
            idempotency_key=create_key, title="杭州周末",
            document=TripDocumentV2.model_validate(valid_document()),
        )
        assert replay["replayed"] is True
        assert replay["conversation_id"] == created["conversation_id"]
        with pytest.raises(TripNotFound):
            service.get_trip_conversation(
                trip_id, tenant_id=tenant_id, owner_id="another-user",
            )
        with pytest.raises(TripCreateIdempotencyConflict):
            service.create_trip(
                tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
                idempotency_key=create_key, title="杭州周末",
                document=TripDocumentV2.model_validate(valid_document()),
                conversation_id="another-conversation",
            )
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM travel_v2.trip_conversations WHERE trip_id = %s::uuid",
                    (trip_id,),
                )
            connection.commit()
        finally:
            connection.close()
        unlinked = service.get_trip_conversation(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )
        assert unlinked["trip_id"] == trip_id
        assert unlinked["conversation_id"] is None
        assert unlinked["linked"] is False
        assert datetime.fromisoformat(unlinked["server_now"]).tzinfo is not None
    finally:
        _cleanup_trip(trip_id)


def test_unlinked_trip_replay_does_not_invent_a_conversation() -> None:
    tenant_id = f"test-{uuid.uuid4().hex[:12]}"
    owner_id = f"user-{uuid.uuid4().hex[:12]}"
    idempotency_key = f"legacy-create-{uuid.uuid4().hex}"
    service = TripService()
    created = service.create_trip(
        tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
        idempotency_key=idempotency_key, title="旧版行程回放",
        document=TripDocumentV2.model_validate(valid_document()),
    )
    trip_id = created["trip_id"]
    try:
        replay = service.create_trip(
            tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
            idempotency_key=idempotency_key, title="旧版行程回放",
            document=TripDocumentV2.model_validate(valid_document()),
        )
        association = service.get_trip_conversation(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )

        assert created["conversation_id"] is None
        assert replay["conversation_id"] is None
        assert replay["replayed"] is True
        assert association["trip_id"] == trip_id
        assert association["conversation_id"] is None
        assert association["linked"] is False
        assert datetime.fromisoformat(association["server_now"]).tzinfo is not None
    finally:
        _cleanup_trip(trip_id)


def test_create_trip_rejects_explicit_blank_conversation_before_connecting() -> None:
    service = TripService(connect=lambda: pytest.fail("blank conversation must fail before database access"))

    with pytest.raises(ValueError, match="conversation_id must not be blank"):
        service.create_trip(
            tenant_id="tenant-test", owner_id="owner-test", actor_id="owner-test",
            idempotency_key=f"blank-{uuid.uuid4().hex}", title="空会话测试",
            document=TripDocumentV2.model_validate(valid_document()),
            conversation_id="   ",
        )


def test_conversation_cannot_be_reused_by_another_trip() -> None:
    tenant_id = f"test-{uuid.uuid4().hex[:12]}"
    owner_id = f"user-{uuid.uuid4().hex[:12]}"
    conversation_id = f"travel-{uuid.uuid4().hex}"
    service = TripService()
    created = service.create_trip(
        tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
        idempotency_key=f"create-{uuid.uuid4().hex}", title="杭州周末",
        document=TripDocumentV2.model_validate(valid_document()),
        conversation_id=conversation_id,
    )
    try:
        with pytest.raises(TripCreateIdempotencyConflict, match="绑定到其他行程"):
            service.create_trip(
                tenant_id=tenant_id, owner_id=owner_id, actor_id=owner_id,
                idempotency_key=f"create-{uuid.uuid4().hex}", title="苏州周末",
                document=TripDocumentV2.model_validate(valid_document()),
                conversation_id=conversation_id,
            )

        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT trip_id FROM travel_v2.trips WHERE tenant_id = %s AND owner_id = %s",
                    (tenant_id, owner_id),
                )
                assert [str(row[0]) for row in cursor.fetchall()] == [created["trip_id"]]
        finally:
            connection.close()
    finally:
        _cleanup_trip(created["trip_id"])


def test_trip_creation_rejects_schedule_conflicts_before_any_insert() -> None:
    tenant_id = f"tenant-{uuid.uuid4().hex[:12]}"
    owner_id = f"user-{uuid.uuid4().hex[:12]}"
    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        TripService().create_trip(
            tenant_id=tenant_id, owner_id=owner_id, title="冲突行程",
            actor_id=owner_id, idempotency_key="conflicting-create",
            document=_two_item_document("10:30"),
        )

    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT trip_id FROM travel_v2.trips "
                "WHERE tenant_id = %s AND owner_id = %s",
                (tenant_id, owner_id),
            )
            unexpected_trip_ids = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                "SELECT count(*) FROM travel_v2.trip_revisions "
                "WHERE tenant_id = %s AND owner_id = %s",
                (tenant_id, owner_id),
            )
            revision_count = cursor.fetchone()[0]
            if unexpected_trip_ids:
                cursor.execute(
                    "DELETE FROM travel_v2.trip_revisions "
                    "WHERE trip_id = ANY(%s)",
                    (unexpected_trip_ids,),
                )
                cursor.execute(
                    "DELETE FROM travel_v2.trips WHERE trip_id = ANY(%s)",
                    (unexpected_trip_ids,),
                )
        connection.commit()
    finally:
        connection.close()
    assert not unexpected_trip_ids
    assert revision_count == 0


def test_structured_and_full_document_writes_share_schedule_gate(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=1,
        idempotency_key="two-item-base", command_type="replace_document",
        change_summary="增加休息时段",
        document=TripDocumentV2.model_validate(_two_item_document()),
    )
    current = TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )["document"]
    command = StructuredTripEditCommand.model_validate({
        "expected_revision": 2, "change_summary": "将休息移到冲突时段",
        "operation": {
            "op": "update_item", "item_id": "item-2", "start_time": "10:30",
        },
    })
    operation = command.operation.model_dump(mode="json", exclude_unset=True)
    proposed = TripEditService._apply_structured_operation(current, operation)

    with pytest.raises(ValueError, match="时间或路程安排冲突") as structured_error:
        edit.apply_operation(
            **scope, actor_id=scope["owner_id"],
            idempotency_key="conflicting-structured", command=command,
        )
    with pytest.raises(ValueError, match="时间或路程安排冲突") as document_error:
        edit.apply_document(
            **scope, actor_id=scope["owner_id"], expected_revision=2,
            idempotency_key="conflicting-document", command_type="replace_document",
            change_summary="替换为冲突行程",
            document=TripDocumentV2.model_validate(proposed),
        )

    assert str(structured_error.value) == str(document_error.value)
    persisted = TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )
    assert persisted["revision"] == 2
    assert persisted["document"] == current
    assert [item["revision"] for item in TripService().list_revisions(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )] == [2, 1]
    assert not {
        "conflicting-structured", "conflicting-document",
    }.intersection(_operation_keys(scope["trip_id"]))


def test_restore_rejects_conflicting_legacy_snapshot_without_writing(
    trip_scope, request,
) -> None:
    scope, _created = trip_scope
    legal_document = TripDocumentV2.model_validate(valid_document()).model_dump(
        mode="json",
    )
    conflicting_document = TripDocumentV2.model_validate(
        _two_item_document("10:30"),
    ).model_dump(mode="json")
    trip_id = uuid.uuid4()

    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            trips = TripRepository()
            revisions = RevisionRepository()
            trips.insert(
                cursor, trip_id=trip_id, tenant_id=scope["tenant_id"],
                owner_id=scope["owner_id"], title="历史冲突行程",
                document=conflicting_document,
            )
            revisions.insert(
                cursor, trip_id=trip_id, tenant_id=scope["tenant_id"],
                owner_id=scope["owner_id"], revision=1, parent_revision=None,
                snapshot=conflicting_document, change_type="create",
                change_summary="历史快照", actor_id=scope["owner_id"],
            )
            assert trips.update_document_cas(
                cursor, trip_id=trip_id, tenant_id=scope["tenant_id"],
                owner_id=scope["owner_id"], expected_revision=1,
                title="已修复行程", document=legal_document,
            ) == 1
            revisions.insert(
                cursor, trip_id=trip_id, tenant_id=scope["tenant_id"],
                owner_id=scope["owner_id"], revision=2, parent_revision=1,
                snapshot=legal_document, change_type="replace_document",
                change_summary="修复行程", actor_id=scope["owner_id"],
            )
        connection.commit()
    finally:
        connection.close()
    seeded_scope = {**scope, "trip_id": str(trip_id)}
    request.addfinalizer(lambda: _cleanup_trip(seeded_scope["trip_id"]))

    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        TripEditService().restore(
            **seeded_scope, actor_id=scope["owner_id"], expected_revision=2,
            target_revision=1, idempotency_key="conflicting-restore",
        )

    persisted = TripService().get_trip(
        seeded_scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )
    assert persisted["revision"] == 2
    assert persisted["document"] == legal_document
    assert [item["revision"] for item in TripService().list_revisions(
        seeded_scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )] == [2, 1]
    assert "conflicting-restore" not in _operation_keys(seeded_scope["trip_id"])


def test_meal_selection_is_inserted_between_morning_and_evening_items(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    document = valid_document()
    morning = document["days"][0]["items"][0]
    morning["duration_min"] = 60
    evening = {
        "item_id": "evening-event", "kind": "activity", "activity_type": "custom",
        "title": "晚间演出", "start_time": "18:00", "duration_min": 90,
        "fixed_start": True, "place": None, "must_visit": False,
        "locked": True, "note": "",
    }
    document["days"][0]["items"].append(evening)
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=1,
        idempotency_key="meal-sequence-base", command_type="replace_document",
        change_summary="设置早晚安排",
        document=TripDocumentV2.model_validate(document),
    )

    result = edit.add_meal(
        **scope, actor_id=scope["owner_id"], idempotency_key="meal-sequence-lunch",
        command={
            "expected_revision": 2, "day_id": "day-1", "meal_period": "lunch",
            "selection_id": stable_selection_id("food", "amap", "food-sequence"),
            "merchant": {
                "merchant_id": "food-sequence", "name": "湖畔餐厅", "source": "amap",
                "observed_at": "2026-10-19T08:00:00+08:00",
            },
        },
    )

    items = result["document"]["days"][0]["items"]
    assert result["revision"] == 3
    assert [(item["title"], item["start_time"]) for item in items] == [
        ("西湖", "09:00"), ("湖畔餐厅", "12:00"), ("晚间演出", "18:00"),
    ]
    assert items[2]["locked"] is True
    assert items[2]["fixed_start"] is True
    assert [(leg["from_item_id"], leg["to_item_id"]) for leg in result["document"]["days"][0]["legs"]] == [
        (items[0]["item_id"], items[1]["item_id"]),
        (items[1]["item_id"], items[2]["item_id"]),
    ]
    assert all(
        leg["reliability"] == "unavailable" and leg["duration_min"] is None
        for leg in result["document"]["days"][0]["legs"]
    )


def test_meal_lodging_and_train_selections_use_applicable_schedule_rules(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    lodging = edit.select_lodging(
        **scope, actor_id=scope["owner_id"], idempotency_key="lodging-valid",
        command=_lodging_command(1, check_out="2026-10-21"),
    )
    assert lodging["revision"] == 2
    assert lodging["document"]["arrangements"]["lodgings"][0]["booking_status"] == "not_booked"

    with pytest.raises(ValidationError, match="lodging dates must fall within"):
        edit.select_lodging(
            **scope, actor_id=scope["owner_id"], idempotency_key="lodging-out-of-range",
            command=_lodging_command(2, check_out="2026-10-22"),
        )
    with pytest.raises(ValidationError, match="首日行程与动车到达后的换乘预留时间冲突"):
        edit.select_intercity_train(
            **scope, actor_id=scope["owner_id"], idempotency_key="train-conflict",
            command=_train_command(2, departure_time="08:00", arrival_time="09:00"),
        )

    train = edit.select_intercity_train(
        **scope, actor_id=scope["owner_id"], idempotency_key="train-valid",
        command=_train_command(2, departure_time="05:00", arrival_time="06:00"),
    )
    assert train["revision"] == 3
    assert train["document"]["arrangements"]["intercity_trains"][0]["duration_min"] == 60

    blocking_document = valid_document()
    blocking_document["days"][0]["items"][0].update({
        "start_time": "08:00", "duration_min": 16 * 60,
    })
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=3,
        idempotency_key="meal-blocker", command_type="replace_document",
        change_summary="设置全天活动",
        document=TripDocumentV2.model_validate(blocking_document),
    )
    with pytest.raises(ValueError, match="当天没有合适的餐饮时间"):
        edit.add_meal(
            **scope, actor_id=scope["owner_id"], idempotency_key="meal-conflict",
            command={
                "expected_revision": 4, "day_id": "day-1", "meal_period": "lunch",
                "selection_id": stable_selection_id("food", "amap", "food-1"),
                "merchant": {
                    "merchant_id": "food-1", "name": "湖畔餐厅", "source": "amap",
                    "observed_at": "2026-10-19T08:00:00+08:00",
                },
            },
        )

    persisted = TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )
    assert persisted["revision"] == 4
    assert len(TripService().list_revisions(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )) == 4
    assert not {
        "lodging-out-of-range", "train-conflict", "meal-conflict",
    }.intersection(_operation_keys(scope["trip_id"]))


def test_edit_is_atomic_revisioned_and_idempotent(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    updated = valid_document()
    updated["days"][0]["title"] = "西湖慢游"

    result = edit.apply_document(
        **scope,
        actor_id=scope["owner_id"],
        expected_revision=1,
        idempotency_key="edit-001",
        command_type="replace_document",
        change_summary="调整第一天安排",
        document=TripDocumentV2.model_validate(updated),
    )
    replay = edit.apply_document(
        **scope,
        actor_id=scope["owner_id"],
        expected_revision=1,
        idempotency_key="edit-001",
        command_type="replace_document",
        change_summary="调整第一天安排",
        document=TripDocumentV2.model_validate(updated),
    )

    assert result["saved"] is True
    assert result["revision"] == 2
    assert replay["revision"] == 2
    assert replay["replayed"] is True
    assert len(TripService().list_revisions(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )) == 2


def test_structured_operation_is_revisioned_idempotent_and_rejects_stale_base(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    command = StructuredTripEditCommand.model_validate({
        "expected_revision": 1,
        "change_summary": "添加休息活动",
        "operation": {
            "op": "add_activity", "day_id": "day-1", "activity_type": "rest",
            "title": "湖边休息", "start_time": "12:00", "duration_min": 30,
        },
    })

    first = edit.apply_operation(
        **scope, actor_id=scope["owner_id"], idempotency_key="structured-add-1",
        command=command,
    )
    replay = edit.apply_operation(
        **scope, actor_id=scope["owner_id"], idempotency_key="structured-add-1",
        command=command,
    )
    stale = StructuredTripEditCommand.model_validate({
        **command.model_dump(mode="json"), "change_summary": "旧版本再次添加",
    })

    assert first["saved"] is True
    assert first["revision"] == 2
    assert replay["replayed"] is True
    assert replay["revision"] == 2
    assert len(first["document"]["days"][0]["items"]) == 2
    with pytest.raises(VersionConflict):
        edit.apply_operation(
            **scope, actor_id=scope["owner_id"], idempotency_key="structured-add-stale",
            command=stale,
        )
    assert TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )["revision"] == 2


def test_conflicting_idempotency_key_and_stale_revision_do_not_mutate_trip(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    first = valid_document()
    first["days"][0]["title"] = "第一次修改"
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=1,
        idempotency_key="same-key", command_type="replace_document",
        change_summary="第一次", document=TripDocumentV2.model_validate(first),
    )

    changed_request = valid_document()
    changed_request["days"][0]["title"] = "另一个内容"
    with pytest.raises(IdempotencyConflict):
        edit.apply_document(
            **scope, actor_id=scope["owner_id"], expected_revision=1,
            idempotency_key="same-key", command_type="replace_document",
            change_summary="不同请求", document=TripDocumentV2.model_validate(changed_request),
        )
    with pytest.raises(VersionConflict):
        edit.apply_document(
            **scope, actor_id=scope["owner_id"], expected_revision=1,
            idempotency_key="stale-key", command_type="replace_document",
            change_summary="过期基准", document=TripDocumentV2.model_validate(changed_request),
        )
    assert TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )["revision"] == 2


def test_restore_creates_new_revision_and_does_not_decrement_revision(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    second = valid_document()
    second["days"][0]["title"] = "新安排"
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=1,
        idempotency_key="edit-restore", command_type="replace_document",
        change_summary="新安排", document=TripDocumentV2.model_validate(second),
    )

    restored = edit.restore(
        **scope, actor_id=scope["owner_id"], expected_revision=2,
        target_revision=1, idempotency_key="restore-001",
    )

    assert restored["revision"] == 3
    assert restored["document"]["days"][0]["title"] == "湖畔漫游"


def test_revision_insert_failure_rolls_back_trip_update(trip_scope) -> None:
    scope, _created = trip_scope
    changed = valid_document()
    changed["days"][0]["title"] = "不能保存"

    class FailingRevisionRepository:
        def insert(self, *_args, **_kwargs):
            raise RuntimeError("injected revision insert failure")

    edit = TripEditService(revision_repository=FailingRevisionRepository())
    with pytest.raises(RuntimeError, match="revision insert"):
        edit.apply_document(
            **scope, actor_id=scope["owner_id"], expected_revision=1,
            idempotency_key="must-roll-back", command_type="replace_document",
            change_summary="注入失败", document=TripDocumentV2.model_validate(changed),
        )

    assert TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )["revision"] == 1
    assert TripService().list_revisions(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )[-1]["revision"] == 1


def test_locked_item_cannot_be_removed_by_a_structured_write(trip_scope) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    locked = valid_document()
    locked["days"][0]["items"][0]["locked"] = True
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=1,
        idempotency_key="lock-item", command_type="structured_edit",
        change_summary="锁定预约景点", document=TripDocumentV2.model_validate(locked),
    )
    removed = valid_document()
    removed["days"][0]["items"] = []

    with pytest.raises(ValueError, match="locked item"):
        edit.apply_document(
            **scope, actor_id=scope["owner_id"], expected_revision=2,
            idempotency_key="remove-locked-item", command_type="structured_edit",
            change_summary="尝试删除锁定项", document=TripDocumentV2.model_validate(removed),
        )

    assert TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )["revision"] == 2


def test_concurrent_same_key_meal_requests_replay_deterministically(
    trip_scope, monkeypatch,
) -> None:
    scope, _created = trip_scope
    edit = TripEditService()
    document = valid_document()
    document["days"][0]["items"][0]["duration_min"] = 60
    document["days"][0]["items"].append({
        "item_id": "evening-event", "kind": "activity", "activity_type": "custom",
        "title": "晚间活动", "start_time": "18:00", "duration_min": 60,
        "fixed_start": True, "place": None, "must_visit": False,
        "locked": True, "note": "",
    })
    edit.apply_document(
        **scope, actor_id=scope["owner_id"], expected_revision=1,
        idempotency_key="meal-concurrency-base", command_type="replace_document",
        change_summary="设置早晚安排",
        document=TripDocumentV2.model_validate(document),
    )

    original_get = edit._get_trip_document
    read_barrier = Barrier(2)

    def read_same_base(*args, **kwargs):
        snapshot = original_get(*args, **kwargs)
        read_barrier.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(edit, "_get_trip_document", read_same_base)
    command = {
        "expected_revision": 2, "day_id": "day-1", "meal_period": "lunch",
        "selection_id": stable_selection_id("food", "amap", "food-concurrent"),
        "merchant": {
            "merchant_id": "food-concurrent", "name": "并发午餐", "source": "amap",
            "observed_at": "2026-10-19T08:00:00+08:00",
        },
    }

    def add_same_meal():
        return edit.add_meal(
            **scope, actor_id=scope["owner_id"], idempotency_key="meal-concurrent",
            command=command,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=20) for future in (
            executor.submit(add_same_meal), executor.submit(add_same_meal),
        )]

    assert sorted(result["replayed"] for result in results) == [False, True]
    assert {result["revision"] for result in results} == {3}
    persisted = TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )
    assert persisted["revision"] == 3
    assert [item["title"] for item in persisted["document"]["days"][0]["items"]] == [
        "西湖", "并发午餐", "晚间活动",
    ]
    assert [item["revision"] for item in TripService().list_revisions(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
    )] == [3, 2, 1]
    assert "meal-concurrent" in _operation_keys(scope["trip_id"])

    monkeypatch.setattr(edit, "_get_trip_document", original_get)
    different_payload = {
        **command,
        "selection_id": stable_selection_id("food", "amap", "food-different"),
        "merchant": {
            "merchant_id": "food-different", "name": "另一家餐厅", "source": "amap",
            "observed_at": "2026-10-19T08:00:00+08:00",
        },
    }
    with pytest.raises(IdempotencyConflict):
        edit.add_meal(
            **scope, actor_id=scope["owner_id"], idempotency_key="meal-concurrent",
            command=different_payload,
        )


def test_two_concurrent_edits_from_same_revision_have_one_winner(trip_scope) -> None:
    scope, _created = trip_scope
    barrier = Barrier(2)

    def submit(title: str, key: str):
        document = valid_document()
        document["days"][0]["title"] = title
        barrier.wait(timeout=5)
        try:
            return TripEditService().apply_document(
                **scope, actor_id=scope["owner_id"], expected_revision=1,
                idempotency_key=key, command_type="structured_edit",
                change_summary=title,
                document=TripDocumentV2.model_validate(document),
            )
        except VersionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda pair: submit(*pair),
            [("并发版本 A", "concurrent-a"), ("并发版本 B", "concurrent-b")],
        ))

    assert sum(isinstance(result, dict) and result["saved"] for result in results) == 1
    assert sum(isinstance(result, VersionConflict) for result in results) == 1
    assert TripService().get_trip(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )["revision"] == 2
    assert len(TripService().list_revisions(
        scope["trip_id"], tenant_id=scope["tenant_id"], owner_id=scope["owner_id"]
    )) == 2
