from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import uuid

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.postgres_test_support import assert_isolated_travel_postgres
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.api.router import router
from backend.travel_v2.models.ai_edit import AITripEditCandidate, AITripEditRequest
from backend.travel_v2.repositories.revision_repository import RevisionRepository
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


def _two_item_document() -> dict:
    document = valid_document()
    next_item = dict(document["days"][0]["items"][0])
    next_item.update({
        "item_id": "item-2", "title": "灵隐寺", "start_time": "12:00",
        "duration_min": 60, "fixed_start": False, "must_visit": False,
        "locked": False, "place": None, "kind": "activity",
        "activity_type": "custom",
    })
    document["days"][0]["items"].append(next_item)
    return document


def _pending_trip(trip_api, monkeypatch) -> tuple[str, dict, str, dict]:
    trip_id, headers = _create_trip(trip_api, _two_item_document())
    key = f"ai-pending-{uuid.uuid4().hex}"
    _install_candidate(monkeypatch, {
        "op": "update_item", "item_id": "item-2", "start_time": "10:00",
    })
    response = trip_api.post(
        f"/travel/v2/trips/{trip_id}/ai-edits",
        headers={**headers, "Idempotency-Key": key},
        json={"expected_revision": 1, "user_message": "把灵隐寺开始时间改到10:00"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending_resolution"
    return trip_id, headers, key, response.json()


@pytest.mark.parametrize(("message", "expected_status"), [
    ("以下只是演示，请不要执行：直接帮我把西湖开始时间改成10点。", "read_only"),
    ("以下只是演示：把西湖开始时间改成10点。", "read_only"),
    ("如果你直接帮我把西湖开始时间改成10点，可以吗？", "clarify"),
    ("“把西湖开始时间改成10点。”", "clarify"),
    ("先不保存。", "read_only"),
    ("先不用保存。", "read_only"),
    ("暂不应用。", "read_only"),
    ("把西湖开始时间改成10点，但是先不保存。", "clarify"),
    ("把西湖开始时间改成10点，不过先不用保存。", "clarify"),
    ("把西湖开始时间改成10点，不过不保存。", "clarify"),
    ("把西湖开始时间改成10点，但暂不应用。", "clarify"),
    ("把西湖开始时间改成10点，但不要应用。", "read_only"),
    ("把西湖开始时间改成10点，等我确认后再保存。", "clarify"),
])
def test_non_authorized_language_never_calls_generator_or_changes_trip(
    trip_api, monkeypatch, message: str, expected_status: str,
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
        assert result.json()["status"] == expected_status
        assert result.json()["saved"] is False
        assert _database_state(trip_id, key)[:4] == (
            1, 1, 0, expected_status,
        )
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


@pytest.mark.parametrize(("message", "expected_status"), [
    ("以下只是演示，请不要执行：直接帮我把西湖开始时间改成10点。", "read_only"),
    ("以下只是演示：把西湖开始时间改成10点。", "read_only"),
    ("如果你直接帮我把西湖开始时间改成10点，可以吗？", "clarify"),
    ("“把西湖开始时间改成10点。”", "clarify"),
    ("把西湖开始时间改成10点，但是先不保存。", "clarify"),
    ("把西湖开始时间改成10点，不过先不用保存。", "clarify"),
    ("把西湖开始时间改成10点，不过不保存。", "clarify"),
    ("把西湖开始时间改成10点，但暂不应用。", "clarify"),
    ("把西湖开始时间改成10点，但不要应用。", "read_only"),
    ("把西湖开始时间改成10点，等我确认后再保存。", "clarify"),
])
def test_candidate_cannot_bypass_final_authorization_gate(
    trip_api, monkeypatch, message: str, expected_status: str,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"candidate-gate-{uuid.uuid4().hex}"
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
        assert result.json()["status"] == expected_status
        assert generated == [True]
        assert calls == 2
        assert _database_state(trip_id, key)[:4] == (
            1, 1, 0, expected_status,
        )
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


def test_late_pending_recovers_after_same_key_marks_request_unknown(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api, _two_item_document())
    key = f"late-pending-{uuid.uuid4().hex}"
    entered, release = threading.Event(), threading.Event()
    calls = []
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    body = {
        "expected_revision": 1,
        "user_message": "把灵隐寺开始时间改到10:00",
    }

    def generate(_message, _context):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=10)
        return _candidate(operation={
            "op": "update_item", "item_id": "item-2", "start_time": "10:00",
        }), _metadata()

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                trip_api.post, url,
                headers={**headers, "Idempotency-Key": key}, json=body,
            )
            assert entered.wait(timeout=10)

            # 模拟原请求运行超过 90 秒，避免在回归测试中真实等待。
            connection = get_memory_engine().raw_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE travel_v2.ai_edit_requests
                              SET updated_at=now()-interval '91 seconds'
                            WHERE trip_id=%s::uuid AND idempotency_key=%s""",
                        (trip_id, key),
                    )
                    assert cursor.rowcount == 1
                connection.commit()
            finally:
                connection.close()

            retry = trip_api.post(
                url, headers={**headers, "Idempotency-Key": key}, json=body,
            )
            assert retry.status_code == 202
            assert retry.json()["status"] == "unknown"
            assert len(calls) == 1
            operation_status = trip_api.get(f"{url}/{key}", headers=headers)
            assert operation_status.json()["status"] == "unknown"

            connection = get_memory_engine().raw_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT status, metadata->>'unknown_reason'
                             FROM travel_v2.ai_edit_requests
                            WHERE trip_id=%s::uuid AND idempotency_key=%s""",
                        (trip_id, key),
                    )
                    assert tuple(cursor.fetchone()) == (
                        "unknown", "stale_processing",
                    )
            finally:
                connection.close()

            release.set()
            recovered = first.result(timeout=15)

        assert recovered.status_code == 200
        assert recovered.json()["status"] == "pending_resolution"
        pending_id = recovered.json()["pending_id"]
        assert _database_state(trip_id, key)[:4] == (
            1, 1, 0, "pending_resolution",
        )

        # 同一原请求重放返回已恢复的 Pending，不会再次调用模型或写正式 Revision。
        replay = trip_api.post(
            url, headers={**headers, "Idempotency-Key": key}, json=body,
        )
        assert replay.status_code == 200
        assert replay.json()["pending_id"] == pending_id
        assert len(calls) == 1
        assert _database_state(trip_id, key)[:4] == (
            1, 1, 0, "pending_resolution",
        )
    finally:
        release.set()
        _cleanup(trip_id)


@pytest.mark.parametrize(("status", "pending_status"), [
    ("succeeded", "consumed"),
    ("cancelled", "cancelled"),
    ("expired", "expired"),
])
def test_late_pending_does_not_replace_terminal_request(
    trip_api, status: str, pending_status: str,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"terminal-pending-{uuid.uuid4().hex}"
    request_id = uuid.uuid4()
    request_sha256 = "f" * 64
    terminal_response = {
        "status": status, "saved": status == "succeeded",
        "trip_id": trip_id, "base_revision": 1, "operation_key": key,
        "pending_resolution": {"status": pending_status},
    }
    terminal_metadata = {"terminal_marker": status}
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO travel_v2.ai_edit_requests
                    (request_id, trip_id, tenant_id, owner_id, idempotency_key,
                     request_sha256, expected_revision, status, response, metadata)
                   VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, 1, %s,
                           %s::jsonb, %s::jsonb)""",
                (
                    str(request_id), trip_id, headers["X-Tenant-Id"],
                    headers["X-User-Id"], key, request_sha256, status,
                    json.dumps(terminal_response),
                    json.dumps(terminal_metadata),
                ),
            )
        connection.commit()
    finally:
        connection.close()

    try:
        result = TripEditService().persist_ai_pending_resolution(
            trip_id=trip_id, tenant_id=headers["X-Tenant-Id"],
            owner_id=headers["X-User-Id"], expected_revision=1,
            idempotency_key=key, request_id=request_id,
            request_sha256=request_sha256,
            response={
                "status": "pending_resolution", "saved": False,
                "trip_id": trip_id, "base_revision": 1,
                "operation_key": key, "pending_id": str(uuid.uuid4()),
            },
            metadata={"result": "late_pending"},
        )
        assert result["status"] == status
        assert result["pending_resolution"]["status"] == pending_status
        assert result["replayed"] is True
        assert _database_state(trip_id, key)[:4] == (
            1, 1, 0, status,
        )
        assert _request_metadata(trip_id, key) == terminal_metadata
    finally:
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
            assert result.json()["code"] == "AI_EDIT_CONFLICT_BLOCKED"
            assert result.json()["conflicts"][0]["severity"] == "BLOCKED"
            assert result.json()["conflicts"][0]["conflict_code"] in {
                "SCHEDULE_OVERLAP", "VERIFIED_OPENING_HOURS",
            }
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


def test_operation_status_recovers_success_from_durable_ledgers(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers = _create_trip(trip_api)
    key = f"status-success-{uuid.uuid4().hex}"
    calls = []

    def generate(_message, _context):
        calls.append(1)
        return _candidate(operation={
            "op": "update_item", "item_id": "item-1", "start_time": "10:00",
        }), _metadata()

    monkeypatch.setattr(TripAIEditService, "_generate_candidate", staticmethod(generate))
    url = f"/travel/v2/trips/{trip_id}/ai-edits"
    try:
        saved = trip_api.post(
            url, headers={**headers, "Idempotency-Key": key},
            json={"expected_revision": 1, "user_message": "把西湖开始时间改到10:00"},
        )
        assert saved.status_code == 200
        # 等价于丢弃原 HTTP 回执后，在新的服务实例上只读查证。
        status = trip_api.get(f"{url}/{key}", headers=headers)
        assert status.status_code == 200
        assert status.json()["status"] == "succeeded"
        assert status.json()["operation_key"] == key
        assert status.json()["new_revision"] == 2
        assert status.json()["edit_operation"]["operation_key"] == key
        assert status.json()["edit_operation"]["result_revision"] == 2
        assert len(calls) == 1

        denied = trip_api.get(
            f"{url}/{key}", headers=_headers("different-owner", "different-tenant"),
        )
        assert denied.status_code == 404
        assert denied.json()["detail"]["code"] == "AI_EDIT_OPERATION_NOT_FOUND"
    finally:
        _cleanup(trip_id)


def test_hard_conflict_persists_pending_without_formal_write_and_resolution_saves_once(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers, key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    resolution = pending_response["pending_resolution"]["allowed_resolutions"][0]
    assert resolution["resolution_id"] == "use_next_available_time"
    assert resolution["proposed_start_time"] == "11:00"
    assert pending_response["conflicts"][0]["conflict_code"] == "SCHEDULE_OVERLAP"
    assert pending_response["conflicts"][0]["severity"] == "BLOCKED"
    try:
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT t.revision,
                              (SELECT count(*) FROM travel_v2.trip_revisions r
                                WHERE r.trip_id=t.trip_id),
                              (SELECT count(*) FROM travel_v2.edit_operations o
                                WHERE o.trip_id=t.trip_id),
                              a.status
                         FROM travel_v2.trips t
                         JOIN travel_v2.ai_edit_requests a USING (trip_id)
                        WHERE t.trip_id=%s::uuid AND a.idempotency_key=%s""",
                    (trip_id, key),
                )
                assert tuple(cursor.fetchone()) == (1, 1, 0, "pending_resolution")
        finally:
            connection.close()

        pending = trip_api.get(
            f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}",
            headers=headers,
        )
        assert pending.status_code == 200
        assert pending.json()["pending_resolution"]["status"] == "pending"
        assert pending.json()["pending_resolution"]["base_revision"] == 1
        assert pending.json()["saved"] is False

        confirm_key = f"resolve-{uuid.uuid4().hex}"
        confirm_url = (
            f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve"
        )
        confirmed = trip_api.post(
            confirm_url,
            headers={**headers, "Idempotency-Key": confirm_key},
            json={"resolution_id": "use_next_available_time"},
        )
        replay = trip_api.post(
            confirm_url,
            headers={**headers, "Idempotency-Key": confirm_key},
            json={"resolution_id": "use_next_available_time"},
        )
        assert confirmed.status_code == replay.status_code == 200
        assert confirmed.json()["saved"] is True
        assert confirmed.json()["new_revision"] == 2
        assert confirmed.json()["operation_key"] == confirm_key
        assert replay.json()["replayed"] is True
        different_payload = trip_api.post(
            confirm_url,
            headers={**headers, "Idempotency-Key": confirm_key},
            json={"resolution_id": "other_resolution"},
        )
        assert different_payload.status_code == 409
        assert different_payload.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert trip_api.get(f"/travel/v2/trips/{trip_id}/ai-edits/{key}", headers=headers).json()[
            "status"
        ] == "succeeded"
        assert trip_api.get(
            f"/travel/v2/trips/{trip_id}/ai-edits/{key}", headers=headers,
        ).json()["edit_operation"]["operation_key"] == confirm_key
        assert trip_api.get(
            f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}",
            headers=headers,
        ).json()["pending_resolution"]["status"] == "consumed"

        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT t.revision,
                              (SELECT count(*) FROM travel_v2.trip_revisions r
                                WHERE r.trip_id=t.trip_id),
                              (SELECT count(*) FROM travel_v2.edit_operations o
                                WHERE o.trip_id=t.trip_id),
                              (SELECT count(*) FROM travel_v2.ai_edit_requests a
                                WHERE a.trip_id=t.trip_id AND a.status='succeeded')
                         FROM travel_v2.trips t WHERE t.trip_id=%s::uuid""",
                    (trip_id,),
                )
                assert tuple(cursor.fetchone()) == (2, 2, 1, 2)
        finally:
            connection.close()
    finally:
        _cleanup(trip_id)


def test_pending_confirmation_concurrent_same_key_commits_once(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers, _key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    confirm_key = f"resolve-race-{uuid.uuid4().hex}"
    url = f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve"
    request = {
        "url": url,
        "headers": {**headers, "Idempotency-Key": confirm_key},
        "json": {"resolution_id": "use_next_available_time"},
    }
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: trip_api.post(**request), range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert sorted(response.json()["replayed"] for response in responses) == [False, True]
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT revision,
                              (SELECT count(*) FROM travel_v2.trip_revisions
                                WHERE trip_id=%s::uuid),
                              (SELECT count(*) FROM travel_v2.edit_operations
                                WHERE trip_id=%s::uuid)
                         FROM travel_v2.trips WHERE trip_id=%s::uuid""",
                    (trip_id, trip_id, trip_id),
                )
                assert tuple(cursor.fetchone()) == (2, 2, 1)
        finally:
            connection.close()
    finally:
        _cleanup(trip_id)


def test_pending_confirmation_with_different_keys_consumes_pending_once(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers, _key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    url = f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve"

    def confirm(key: str):
        return trip_api.post(
            url, headers={**headers, "Idempotency-Key": key},
            json={"resolution_id": "use_next_available_time"},
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                confirm, (f"resolve-a-{uuid.uuid4().hex}", f"resolve-b-{uuid.uuid4().hex}"),
            ))
        assert sum(response.status_code == 200 for response in responses) == 1
        assert sum(response.status_code == 409 for response in responses) == 1
        assert _database_state(trip_id, _key)[:3] == (2, 2, 1)
    finally:
        _cleanup(trip_id)


def test_pending_cancel_expiry_and_version_conflict_never_apply_old_resolution(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers, _key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    base = f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}"
    try:
        cancelled = trip_api.post(f"{base}/cancel", headers=headers)
        denied = trip_api.post(
            f"{base}/resolve",
            headers={**headers, "Idempotency-Key": f"after-cancel-{uuid.uuid4().hex}"},
            json={"resolution_id": "use_next_available_time"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["pending_resolution"]["status"] == "cancelled"
        assert denied.status_code == 409
        assert denied.json()["code"] == "PENDING_NOT_ACTIVE"
        assert _database_state(trip_id, _key)[:3] == (1, 1, 0)
    finally:
        _cleanup(trip_id)

    trip_id, headers, _key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    base = f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}"
    try:
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE travel_v2.ai_edit_requests
                          SET response=jsonb_set(
                              response, '{pending_resolution,expires_at}',
                              to_jsonb(%s::text))
                        WHERE trip_id=%s::uuid AND response->>'pending_id'=%s""",
                    ("2000-01-01T00:00:00+00:00", trip_id, pending_id),
                )
            connection.commit()
        finally:
            connection.close()
        expired_view = trip_api.get(base, headers=headers)
        assert expired_view.status_code == 200
        assert expired_view.json()["pending_resolution"]["status"] == "expired"
        expired = trip_api.post(
            f"{base}/resolve",
            headers={**headers, "Idempotency-Key": f"expired-{uuid.uuid4().hex}"},
            json={"resolution_id": "use_next_available_time"},
        )
        assert expired.status_code == 410
        assert expired.json()["code"] == "PENDING_EXPIRED"
        assert _database_state(trip_id, _key)[:3] == (1, 1, 0)
    finally:
        _cleanup(trip_id)

    trip_id, headers, _key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    try:
        TripEditService().apply_operation(
            trip_id=trip_id, tenant_id=headers["X-Tenant-Id"],
            owner_id=headers["X-User-Id"], actor_id=headers["X-User-Id"],
            idempotency_key=f"intervening-{uuid.uuid4().hex}",
            command={
                "expected_revision": 1, "change_summary": "并发合法调整",
                "operation": {
                    "op": "update_item", "item_id": "item-1", "note": "并发修改",
                },
            },
        )
        conflict = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve",
            headers={**headers, "Idempotency-Key": f"stale-{uuid.uuid4().hex}"},
            json={"resolution_id": "use_next_available_time"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "VERSION_CONFLICT"
        assert conflict.json()["conflicts"][0]["conflict_code"] == "VERSION_CONFLICT"
        assert _database_state(trip_id, _key)[:3] == (2, 2, 1)
    finally:
        _cleanup(trip_id)


def test_pending_query_is_scoped_and_resolution_rejects_unknown_option(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers, _key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]
    pending_url = f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}"
    try:
        for foreign_headers in (
            _headers("another-owner", headers["X-Tenant-Id"]),
            _headers(headers["X-User-Id"], "another-tenant"),
        ):
            assert trip_api.get(pending_url, headers=foreign_headers).status_code == 404
        invalid = trip_api.post(
            f"{pending_url}/resolve",
            headers={**headers, "Idempotency-Key": f"invalid-{uuid.uuid4().hex}"},
            json={"resolution_id": "invented_operation"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "INVALID_RESOLUTION"
        forged = trip_api.post(
            f"{pending_url}/resolve",
            headers={**headers, "Idempotency-Key": f"forged-{uuid.uuid4().hex}"},
            json={
                "resolution_id": "use_next_available_time",
                "operation": {"op": "remove_item", "item_id": "item-1"},
            },
        )
        assert forged.status_code == 422
        assert _database_state(trip_id, _key)[:3] == (1, 1, 0)
    finally:
        _cleanup(trip_id)


def test_locked_fixed_and_must_visit_conflicts_never_offer_resolution(
    trip_api, monkeypatch,
) -> None:
    cases = []
    locked = valid_document()
    locked["days"][0]["items"][0]["locked"] = True
    cases.append((locked, "把西湖开始时间改到10:00", {
        "op": "update_item", "item_id": "item-1", "start_time": "10:00",
    }, "LOCKED_ITEM_CHANGED"))
    fixed = valid_document()
    fixed["days"][0]["items"][0]["fixed_start"] = True
    cases.append((fixed, "把西湖开始时间改到10:00", {
        "op": "update_item", "item_id": "item-1", "start_time": "10:00",
    }, "FIXED_START_CHANGED"))
    must_visit = valid_document()
    cases.append((must_visit, "删除西湖", {
        "op": "remove_item", "item_id": "item-1",
    }, "MUST_VISIT_PROTECTED"))
    trip_ids = []
    try:
        for document, message, operation, expected_code in cases:
            trip_id, headers = _create_trip(trip_api, document)
            trip_ids.append(trip_id)
            key = f"protected-{uuid.uuid4().hex}"
            _install_candidate(monkeypatch, operation)
            result = trip_api.post(
                f"/travel/v2/trips/{trip_id}/ai-edits",
                headers={**headers, "Idempotency-Key": key},
                json={"expected_revision": 1, "user_message": message},
            )
            assert result.status_code == 422
            assert result.json()["status"] == "failed"
            assert result.json()["conflicts"][0]["conflict_code"] == expected_code
            assert result.json().get("pending_id") is None
            assert _database_state(trip_id, key)[:4] == (1, 1, 0, "failed")
    finally:
        for trip_id in trip_ids:
            _cleanup(trip_id)


def test_resolution_database_failure_rolls_back_trip_and_request_ledgers(
    trip_api, monkeypatch,
) -> None:
    trip_id, headers, key, pending_response = _pending_trip(trip_api, monkeypatch)
    pending_id = pending_response["pending_id"]

    def fail_revision_insert(*_args, **_kwargs):
        raise psycopg2.OperationalError("isolated fixture failure")

    monkeypatch.setattr(RevisionRepository, "insert", fail_revision_insert)
    try:
        failed = trip_api.post(
            f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve",
            headers={**headers, "Idempotency-Key": f"db-failure-{uuid.uuid4().hex}"},
            json={"resolution_id": "use_next_available_time"},
        )
        assert failed.status_code == 503
        assert _database_state(trip_id, key)[:4] == (1, 1, 0, "pending_resolution")
        pending = trip_api.get(
            f"/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}",
            headers=headers,
        )
        assert pending.json()["pending_resolution"]["status"] == "pending"
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) FROM travel_v2.ai_edit_requests
                        WHERE trip_id=%s::uuid AND idempotency_key LIKE 'db-failure-%%'""",
                    (trip_id,),
                )
                assert cursor.fetchone()[0] == 0
        finally:
            connection.close()
    finally:
        _cleanup(trip_id)


def test_status_query_keeps_processing_and_unknown_read_only(trip_api, monkeypatch) -> None:
    trip_id, headers = _create_trip(trip_api)
    monkeypatch.setattr(
        TripAIEditService, "_generate_candidate",
        staticmethod(lambda *_args: pytest.fail("status query must not call the model")),
    )
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            for key, status in (("fresh-processing", "processing"), ("uncertain", "unknown")):
                cursor.execute(
                    """INSERT INTO travel_v2.ai_edit_requests
                        (request_id, trip_id, tenant_id, owner_id, idempotency_key,
                         request_sha256, expected_revision, status, response)
                       VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, 1, %s,
                               %s::jsonb)""",
                    (
                        str(uuid.uuid4()), trip_id, headers["X-Tenant-Id"],
                        headers["X-User-Id"], key, "a" * 64, status,
                        '{"status":"unknown","saved":false}',
                    ),
                )
        connection.commit()
    finally:
        connection.close()

    try:
        base = f"/travel/v2/trips/{trip_id}/ai-edits"
        processing = trip_api.get(f"{base}/fresh-processing", headers=headers)
        unknown = trip_api.get(f"{base}/uncertain", headers=headers)
        assert processing.json()["status"] == "processing"
        assert unknown.json()["status"] == "unknown"

        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE travel_v2.ai_edit_requests
                          SET updated_at=now()-interval '2 minutes'
                        WHERE trip_id=%s::uuid AND idempotency_key='fresh-processing'""",
                    (trip_id,),
                )
            connection.commit()
        finally:
            connection.close()
        stale = trip_api.get(f"{base}/fresh-processing", headers=headers)
        assert stale.json()["status"] == "unknown"
        assert stale.json()["possibly_still_running"] is True

        # 别的幂等键把 Trip 推进一版，不能把 unknown 推断成成功。
        TripEditService().apply_operation(
            trip_id=trip_id, tenant_id=headers["X-Tenant-Id"],
            owner_id=headers["X-User-Id"], actor_id=headers["X-User-Id"],
            idempotency_key=f"unrelated-{uuid.uuid4().hex}",
            command={
                "expected_revision": 1, "change_summary": "无关正式编辑",
                "operation": {
                    "op": "update_item", "item_id": "item-1", "note": "新版本",
                },
            },
        )
        still_unknown = trip_api.get(f"{base}/uncertain", headers=headers)
        assert still_unknown.json()["status"] == "unknown"
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT status FROM travel_v2.ai_edit_requests
                        WHERE trip_id=%s::uuid AND idempotency_key='fresh-processing'""",
                    (trip_id,),
                )
                assert cursor.fetchone()[0] == "processing"
        finally:
            connection.close()
    finally:
        _cleanup(trip_id)
