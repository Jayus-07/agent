from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import uuid

import pytest

from backend.config.database import MEMORY_DB_CONFIG
from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.models.commands import StructuredTripEditCommand
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.services.trip_edit_service import (
    IdempotencyConflict,
    TripEditService,
    VersionConflict,
)
from backend.travel_v2.services.trip_service import TripNotFound, TripService


pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires the explicitly selected local agent_memory PostgreSQL",
)


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


@pytest.fixture
def trip_scope():
    assert MEMORY_DB_CONFIG["dbname"] == "agent_memory"
    assert MEMORY_DB_CONFIG["port"] == 5433
    scope = {
        "tenant_id": f"test-{uuid.uuid4().hex[:12]}",
        "owner_id": f"user-{uuid.uuid4().hex[:12]}",
        "title": "杭州周末",
    }
    trip = TripService().create_trip(
        tenant_id=scope["tenant_id"], owner_id=scope["owner_id"],
        title=scope["title"],
        actor_id=scope["owner_id"],
        document=TripDocumentV2.model_validate(valid_document()),
        idempotency_key=f"create-{uuid.uuid4().hex}",
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
