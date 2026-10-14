from __future__ import annotations

import concurrent.futures
import os
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.postgres_test_support import assert_isolated_travel_postgres
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.api.router import router
from backend.travel_v2.models.ai_edit import AITripEditCandidate, AITripEditRequest
from backend.travel_v2.repositories.trip_repository import TripRepository
from backend.travel_v2.services.trip_ai_edit_service import (
    AIEditModelOutputError,
    TripAIEditService,
)
from backend.travel_v2.services.trip_edit_service import (
    IdempotencyConflict,
    TripEditService,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires an explicitly selected isolated travel V2 PostgreSQL",
)


@pytest.fixture(autouse=True)
def _isolated_postgres_guard() -> None:
    assert_isolated_travel_postgres()


@pytest.fixture
def trip_api():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _headers(user_id: str, tenant_id: str) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Auth-Type": "jwt",
        "X-Tenant-Id": tenant_id,
    }


def _cleanup(trip_id: str) -> None:
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM travel_v2.ai_edit_requests WHERE trip_id=%s::uuid",
                (trip_id,),
            )
            cursor.execute(
                "DELETE FROM travel_v2.edit_operations WHERE trip_id=%s::uuid",
                (trip_id,),
            )
            cursor.execute(
                "DELETE FROM travel_v2.trip_revisions WHERE trip_id=%s::uuid",
                (trip_id,),
            )
            cursor.execute(
                "DELETE FROM travel_v2.trips WHERE trip_id=%s::uuid",
                (trip_id,),
            )
        connection.commit()
    finally:
        connection.close()


def _create_trip(
    client: TestClient, document: dict | None = None,
) -> tuple[str, dict[str, str]]:
    owner = f"ai-owner-{uuid.uuid4().hex[:12]}"
    tenant = f"ai-tenant-{uuid.uuid4().hex[:12]}"
    created = client.post(
        "/travel/v2/trips",
        headers={**_headers(owner, tenant), "Idempotency-Key": f"create-{uuid.uuid4().hex}"},
        json={"title": "杭州一日", "document": document or valid_document()},
    )
    assert created.status_code == 201
    return created.json()["trip_id"], _headers(owner, tenant)


def _candidate(*, operation: dict | None = None, outcome: str = "candidate"):
    body = {"outcome": outcome}
    if outcome == "candidate":
        body.update({"change_summary": "调整西湖开始时间", "operation": operation})
    elif outcome == "clarify":
        body["clarification"] = "请问要调整到几点？"
    return AITripEditCandidate.model_validate(body)


def _metadata() -> dict:
    return {
        "role": "main", "model": "fixture-model",
        "prompt_version": "travel.v2.trip_ai_edit@3",
        "latency_ms": 4, "input_tokens": 20, "output_tokens": 10,
    }


def _install_candidate(monkeypatch, operation: dict) -> None:
    monkeypatch.setattr(
        TripAIEditService, "_generate_candidate",
        staticmethod(lambda _message, _context: (_candidate(operation=operation), _metadata())),
    )


def _database_state(trip_id: str, key: str) -> tuple[int, int, int, str, dict]:
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT revision FROM travel_v2.trips WHERE trip_id=%s::uuid",
                (trip_id,),
            )
            revision = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT count(*) FROM travel_v2.trip_revisions WHERE trip_id=%s::uuid",
                (trip_id,),
            )
            revisions = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT count(*) FROM travel_v2.edit_operations WHERE trip_id=%s::uuid",
                (trip_id,),
            )
            operations = int(cursor.fetchone()[0])
            cursor.execute(
                """SELECT status, response::text FROM travel_v2.ai_edit_requests
                    WHERE trip_id=%s::uuid AND idempotency_key=%s""",
                (trip_id, key),
            )
            status, response = cursor.fetchone()
            import json

            return revision, revisions, operations, status, json.loads(response)
    finally:
        connection.close()


def _request_metadata(trip_id: str, key: str) -> dict:
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT metadata::text FROM travel_v2.ai_edit_requests
                    WHERE trip_id=%s::uuid AND idempotency_key=%s""",
                (trip_id, key),
            )
            import json

            return json.loads(cursor.fetchone()[0])
    finally:
        connection.close()


@pytest.mark.parametrize("message", [
    "如果把西湖开始时间改成10:00，会不会太赶？",
    "你觉得把西湖改到10点合适吗？",
    "先不要修改，告诉我改成10点会怎样。",
])
def test_hypothetical_question_never_calls_generator_or_changes_trip(
    trip_api, monkeypatch, message: str,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"read-{uuid.uuid4().hex}"
    monkeypatch.setattr(
        TripAIEditService, "_generate_candidate",
        staticmethod(lambda *_args: pytest.fail("read_only must not call the edit model")),
    )
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": message},
        )
        assert result.status_code == 200
        assert result.json()["status"] == "read_only"
        assert result.json()["saved"] is False
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "read_only")
    finally:
        _cleanup(trip_id)


@pytest.mark.parametrize("message", [
    "把西湖开始时间改成10:00。",
    "如果可以的话，请直接把西湖改成10:00。",
])
def test_explicit_execution_authorization_saves_one_revision(
    trip_api, monkeypatch, message: str,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"authorized-{uuid.uuid4().hex}"
    _install_candidate(
        monkeypatch,
        {"op": "update_item", "item_id": "item-1", "start_time": "10:00"},
    )
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": message},
        )
        assert result.status_code == 200
        assert result.json()["status"] == "saved"
        assert result.json()["new_revision"] == 2
        assert _database_state(trip_id, key)[:4] == (2, 2, 1, "succeeded")
    finally:
        _cleanup(trip_id)


def test_consultative_candidate_cannot_bypass_final_authorization_gate(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"candidate-gate-{uuid.uuid4().hex}"
    message = "如果把西湖开始时间改成10:00，会不会太赶？"
    generated = []
    calls = 0
    original_authorization = TripAIEditService._execution_authorization

    def force_first_gate(_message: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "authorized"
        return original_authorization(_message)

    def generate(_message: str, _context: dict):
        generated.append(True)
        return _candidate(operation={
            "op": "update_item", "item_id": "item-1", "start_time": "10:00",
        }), _metadata()

    monkeypatch.setattr(
        TripAIEditService, "_execution_authorization", staticmethod(force_first_gate),
    )
    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": message},
        )
        assert result.status_code == 200
        assert result.json()["status"] == "read_only"
        assert generated == [True]
        assert calls == 2
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "read_only")
    finally:
        _cleanup(trip_id)


def test_pre_call_budget_rejection_is_failed_and_same_key_never_regenerates(
    trip_api, monkeypatch,
) -> None:
    from types import SimpleNamespace

    from backend.config import model_roles
    from backend.infra import async_utils
    from backend.infra.llm import proxy
    from backend.infra.llm.budget import RequestBudgetExceeded
    from backend.prompts.service import prompt_service

    trip_id, headers = _create_trip(trip_api)
    key = f"budget-{uuid.uuid4().hex}"
    calls = []

    class BudgetFixture:
        def invoke(self, _messages):
            calls.append(True)
            raise RequestBudgetExceeded("request_calls")

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
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    body = {"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"}
    try:
        first = trip_api.post(url, headers={**headers, "Idempotency-Key": key}, json=body)
        replay = trip_api.post(url, headers={**headers, "Idempotency-Key": key}, json=body)
        assert first.status_code == replay.status_code == 503
        assert first.json()["status"] == replay.json()["status"] == "failed"
        assert first.json()["code"] == "AI_EDIT_PRECALL_REJECTED"
        assert len(calls) == 1
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "failed")
        assert _request_metadata(trip_id, key)["result"] == "pre_call_rejected"
    finally:
        _cleanup(trip_id)


def test_invalid_model_output_does_not_add_revision(trip_api, monkeypatch) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"invalid-output-{uuid.uuid4().hex}"
    monkeypatch.setattr(
        TripAIEditService, "_generate_candidate",
        staticmethod(lambda *_args: (_ for _ in ()).throw(
            AIEditModelOutputError("invalid fixture", {"role": "main"}),
        )),
    )
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"},
        )
        assert result.status_code == 502
        assert result.json()["code"] == "AI_EDIT_INVALID_OUTPUT"
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "failed")
    finally:
        _cleanup(trip_id)


def test_ai_edit_success_same_key_replay_and_different_payload_conflict(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"ai-{uuid.uuid4().hex}"
    calls = []
    operation = {"op": "update_item", "item_id": "item-1", "start_time": "10:00"}

    def generate(_message, _context):
        calls.append(1)
        return _candidate(operation=operation), _metadata()

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    request_body = {"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"}
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    try:
        saved = trip_api.post(url, headers={**headers, "Idempotency-Key": key}, json=request_body)
        replay = trip_api.post(url, headers={**headers, "Idempotency-Key": key}, json=request_body)
        conflict = trip_api.post(
            url, headers={**headers, "Idempotency-Key": key},
            json={**request_body, "user_message": "把西湖开始时间改到11:00"},
        )

        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        assert saved.json()["base_revision"] == 1
        assert saved.json()["new_revision"] == 2
        assert saved.json()["operation_key"] == key
        assert saved.json()["document"]["days"][0]["items"][0]["start_time"] == "10:00"
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert conflict.status_code == 409
        assert len(calls) == 1
        revision, revisions, operations, status, receipt = _database_state(trip_id, key)
        assert (revision, revisions, operations, status) == (2, 2, 1, "succeeded")
        assert receipt["new_revision"] == 2
        metadata = _request_metadata(trip_id, key)
        assert metadata["role"] == "main"
        assert metadata["model"] == "fixture-model"
        assert metadata["prompt_version"] == "travel.v2.trip_ai_edit@3"
        assert metadata["latency_ms"] == 4
        assert metadata["input_tokens"] == 20
        assert metadata["output_tokens"] == 10
        assert metadata["operation_type"] == "update_item"
        assert metadata["result"] == "candidate_validated"
    finally:
        _cleanup(trip_id)


def test_same_key_concurrent_request_only_generates_one_candidate(trip_api, monkeypatch) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"concurrent-{uuid.uuid4().hex}"
    entered, release = threading.Event(), threading.Event()
    calls = []

    def generate(_message, _context):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=10)
        return _candidate(operation={
            "op": "update_item", "item_id": "item-1", "start_time": "10:00",
        }), _metadata()

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    body = {"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                trip_api.post, url,
                headers={**headers, "Idempotency-Key": key}, json=body,
            )
            assert entered.wait(timeout=10)
            second = trip_api.post(
                url, headers={**headers, "Idempotency-Key": key}, json=body,
            )
            assert second.status_code == 202
            assert second.json()["status"] == "processing"
            assert len(calls) == 1
            release.set()
            first_response = first.result(timeout=15)
        assert first_response.status_code == 200
        assert _database_state(trip_id, key)[:4] == (2, 2, 1, "succeeded")
    finally:
        release.set()
        _cleanup(trip_id)


def test_ai_reservation_rechecks_operation_ledger_after_trip_lock(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    user_id, tenant_id = headers["X-User-Id"], headers["X-Tenant-Id"]
    key = f"cross-ledger-{uuid.uuid4().hex}"
    entered_lock, release_lock = threading.Event(), threading.Event()
    calls = []

    class PausingTripRepository(TripRepository):
        def get(self, cursor, trip_id, *, tenant_id, owner_id, for_update=False):
            if for_update:
                entered_lock.set()
                assert release_lock.wait(timeout=10)
            return super().get(
                cursor, trip_id, tenant_id=tenant_id,
                owner_id=owner_id, for_update=for_update,
            )

    service = TripAIEditService(trip_repository=PausingTripRepository())
    monkeypatch.setattr(
        TripAIEditService, "_generate_candidate",
        staticmethod(lambda *_args: calls.append(1)),
    )
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                service.edit,
                trip_id=trip_id, tenant_id=tenant_id, owner_id=user_id,
                actor_id=user_id, idempotency_key=key,
                request=AITripEditRequest(
                    expected_revision=1,
                    user_message="把西湖开始时间改到10:00",
                ),
            )
            assert entered_lock.wait(timeout=10)
            TripEditService().apply_operation(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=user_id,
                actor_id=user_id, idempotency_key=key,
                command={
                    "expected_revision": 1,
                    "change_summary": "先提交结构化编辑",
                    "operation": {
                        "op": "update_item", "item_id": "item-1",
                        "note": "结构化操作占用该 key",
                    },
                },
            )
            release_lock.set()
            with pytest.raises(IdempotencyConflict):
                pending.result(timeout=15)
        assert calls == []
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT revision,
                              (SELECT count(*) FROM travel_v2.trip_revisions
                                WHERE trip_id = %s::uuid),
                              (SELECT count(*) FROM travel_v2.edit_operations
                                WHERE trip_id = %s::uuid),
                              (SELECT count(*) FROM travel_v2.ai_edit_requests
                                WHERE trip_id = %s::uuid)
                         FROM travel_v2.trips WHERE trip_id = %s::uuid""",
                    (trip_id, trip_id, trip_id, trip_id),
                )
                assert tuple(cursor.fetchone()) == (2, 2, 1, 0)
        finally:
            connection.close()
    finally:
        release_lock.set()
        _cleanup(trip_id)


def test_two_different_ai_keys_on_same_revision_obey_cas(trip_api, monkeypatch) -> None:
    trip_id, headers = _create_trip(trip_api)
    barrier = threading.Barrier(2)
    calls = []

    def generate(_message, _context):
        calls.append(1)
        barrier.wait(timeout=10)
        return _candidate(operation={
            "op": "update_item", "item_id": "item-1", "start_time": "10:00",
        }), _metadata()

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    bodies = [
        {"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"},
        {"expected_revision": 1, "user_message": "把西湖开始时间调整到10:00"},
    ]
    keys = [f"cas-a-{uuid.uuid4().hex}", f"cas-b-{uuid.uuid4().hex}"]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    trip_api.post, url,
                    headers={**headers, "Idempotency-Key": key}, json=body,
                )
                for key, body in zip(keys, bodies, strict=True)
            ]
            responses = [future.result(timeout=20) for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(calls) == 2
        revision, revisions, operations, _status, _response = _database_state(trip_id, keys[0])
        assert (revision, revisions, operations) == (2, 2, 1)
        statuses = []
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT status FROM travel_v2.ai_edit_requests
                        WHERE trip_id=%s::uuid AND idempotency_key = ANY(%s)""",
                    (trip_id, keys),
                )
                statuses = sorted(row[0] for row in cursor.fetchall())
        finally:
            connection.close()
        assert statuses == ["failed", "succeeded"]
    finally:
        _cleanup(trip_id)


def test_invalid_scope_validator_conflict_and_protected_item_leave_no_revision(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"protected-{uuid.uuid4().hex}"
    _install_candidate(monkeypatch, {"op": "remove_item", "item_id": "item-1"})
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": "删除西湖"},
        )
        assert result.status_code == 422
        assert result.json()["status"] == "failed"
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "failed")
    finally:
        _cleanup(trip_id)


def test_schedule_opening_and_verified_route_conflicts_do_not_write(
    trip_api, monkeypatch,
) -> None:
    overlap_document = valid_document()
    overlap_item = {
        "item_id": "item-afternoon", "kind": "activity",
        "activity_type": "custom", "title": "下午活动",
        "start_time": "14:00", "duration_min": 60,
        "fixed_start": False, "place": None, "must_visit": False,
        "locked": False, "note": "",
    }
    overlap_document["days"][0]["items"].append(overlap_item)

    hours_document = valid_document()
    hours_document["days"][0]["items"][0]["place"]["facts"]["opening_hours"] = {
        "open_time": "08:00", "close_time": "18:00", "closed_weekdays": [],
    }

    route_document = valid_document()
    route_item = {
        "item_id": "item-route-destination", "kind": "place",
        "title": "路线后的景点", "start_time": "12:30",
        "duration_min": 60, "fixed_start": False,
        "place": {
            "place_id": "route-destination", "name": "灵隐寺",
            "lat": 30.24, "lng": 120.15, "address": "杭州市西湖区",
            "facts": {
                "verification": "verified", "source": "test-fixture",
                "observed_at": "2026-10-09T08:00:00+08:00",
            },
        },
        "must_visit": False, "locked": False, "note": "",
    }
    route_document["days"][0]["items"].append(route_item)
    route_document["days"][0]["legs"] = [{
        "leg_id": "verified-route", "from_item_id": "item-1",
        "to_item_id": "item-route-destination", "selected_mode": "transit",
        "duration_min": 60, "distance_m": 5000, "cost_cny": 2,
        "reliability": "verified", "source": "route-provider",
        "observed_at": "2026-10-09T08:00:00+08:00",
    }]

    cases = [
        (
            overlap_document,
            "把西湖开始时间改到14:00",
            {"op": "update_item", "item_id": "item-1", "start_time": "14:00"},
        ),
        (
            hours_document,
            "把西湖开始时间改到19:00",
            {"op": "update_item", "item_id": "item-1", "start_time": "19:00"},
        ),
        (
            route_document,
            "把西湖停留时长改为180分钟",
            {"op": "update_item", "item_id": "item-1", "duration_min": 180},
        ),
    ]
    created_trip_ids = []
    try:
        for document, message, operation in cases:
            trip_id, headers = _create_trip(trip_api, document)
            created_trip_ids.append(trip_id)
            key = f"validator-{uuid.uuid4().hex}"
            _install_candidate(monkeypatch, operation)
            result = trip_api.post(
                f"/travel/v2/trips/{trip_id}/ai-edits",
                headers={**headers, "Idempotency-Key": key},
                json={"expected_revision": 1, "user_message": message},
            )
            assert result.status_code == 422
            assert result.json()["code"] == "AI_EDIT_VALIDATION_REJECTED"
            assert _database_state(trip_id, key)[:4] == (1, 1, 0, "failed")
    finally:
        for trip_id in created_trip_ids:
            _cleanup(trip_id)


def test_multi_operation_request_is_rejected_without_partial_success(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"multi-{uuid.uuid4().hex}"
    _install_candidate(monkeypatch, {"op": "remove_item", "item_id": "item-1"})
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={
                "expected_revision": 1,
                "user_message": "删除西湖，但再增加一个休息时间",
            },
        )
        assert result.status_code == 422
        assert result.json()["code"] == "AI_EDIT_CANDIDATE_REJECTED"
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "failed")
    finally:
        _cleanup(trip_id)


def test_replace_place_derives_selection_id_and_commits_current_snapshot(
    trip_api, monkeypatch,
) -> None:
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
    trip_id, headers = _create_trip(trip_api, document)
    key = f"replace-{uuid.uuid4().hex}"
    _install_candidate(monkeypatch, {
        "op": "replace_place", "item_id": "item-1", "place": destination,
    })
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={
                "expected_revision": 1,
                "user_message": "把西湖换成灵隐寺",
            },
        )
        assert result.status_code == 200
        assert result.json()["saved"] is True
        assert result.json()["new_revision"] == 2
        assert result.json()["document"]["days"][0]["items"][0]["place"]["place_id"] == "lingyin"
        assert _database_state(trip_id, key)[:4] == (2, 2, 1, "succeeded")
    finally:
        _cleanup(trip_id)


def test_trip_change_during_generation_fails_cas_without_ai_revision(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"cas-{uuid.uuid4().hex}"
    user_id, tenant_id = headers["X-User-Id"], headers["X-Tenant-Id"]

    def generate(_message, _context):
        TripEditService().apply_operation(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=user_id,
            actor_id=user_id, idempotency_key=f"other-{uuid.uuid4().hex}",
            command={
                "expected_revision": 1,
                "change_summary": "并发的结构化编辑",
                "operation": {
                    "op": "update_item", "item_id": "item-1",
                    "note": "保留这次并发写入",
                },
            },
        )
        return _candidate(operation={
            "op": "update_item", "item_id": "item-1", "start_time": "10:00",
        }), _metadata()

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    try:
        result = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits",
            headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"},
        )
        assert result.status_code == 409
        assert result.json()["code"] == "VERSION_CONFLICT"
        revision, revisions, operations, status, _response = _database_state(trip_id, key)
        assert (revision, revisions, operations, status) == (2, 2, 1, "failed")
    finally:
        _cleanup(trip_id)


def test_timeout_is_unknown_and_same_key_does_not_regenerate(trip_api, monkeypatch) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"timeout-{uuid.uuid4().hex}"
    calls = []

    def timeout(_message, _context):
        calls.append(1)
        raise TimeoutError("simulated provider timeout")

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(timeout))
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    body = {"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"}
    try:
        first = trip_api.post(url, headers={**headers, "Idempotency-Key": key}, json=body)
        replay = trip_api.post(url, headers={**headers, "Idempotency-Key": key}, json=body)
        assert first.status_code == replay.status_code == 202
        assert first.json()["status"] == replay.json()["status"] == "unknown"
        assert len(calls) == 1
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "unknown")
    finally:
        _cleanup(trip_id)


def test_other_owner_cannot_start_trip_ai_edit(trip_api, monkeypatch) -> None:
    trip_id, owner_headers = _create_trip(trip_api)
    key = f"denied-{uuid.uuid4().hex}"
    monkeypatch.setattr(
        TripAIEditService, "_generate_candidate",
        staticmethod(lambda *_args: pytest.fail("unauthorized calls cannot reach model")),
    )
    try:
        for user_id, tenant_id in (
            (f"other-{uuid.uuid4().hex[:8]}", owner_headers["X-Tenant-Id"]),
            (owner_headers["X-User-Id"], f"wrong-{uuid.uuid4().hex[:8]}"),
        ):
            denied = trip_api.post(
                f"/travel/v2/trips/{trip_id}/ai-edits",
                headers={**_headers(user_id, tenant_id),
                         "Idempotency-Key": f"{key}-{uuid.uuid4().hex[:4]}"},
                json={"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"},
            )
            assert denied.status_code == 404
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM travel_v2.ai_edit_requests WHERE trip_id=%s::uuid",
                    (trip_id,),
                )
                assert cursor.fetchone()[0] == 0
        finally:
            connection.close()
    finally:
        _cleanup(trip_id)
