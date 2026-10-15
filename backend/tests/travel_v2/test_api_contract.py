from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.api.router import router
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.services.export_service import RevisionExportService
from backend.travel_v2.services.share_service import ShareNotFoundError, ShareService
from backend.travel_v2.services.trip_edit_service import TripEditService, VersionConflict
from backend.travel_v2.services.trip_service import (
    IdempotencyConflict,
    TripNotFound,
    TripService,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_v2_router_openapi_contains_trip_and_template_routes():
    schema = _client().get("/openapi.json").json()

    assert "/travel/v2/trips" in schema["paths"]
    assert "/travel/v2/templates/{template_key}/copy" in schema["paths"]
    assert "/travel/v2/search/food" in schema["paths"]
    assert "/travel/v2/search/hotels" in schema["paths"]
    assert "/travel/v2/search/trains" in schema["paths"]
    assert "/travel/v2/search/places" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/edits" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/selections/meals" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/selections/lodgings" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/selections/intercity-trains" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/shares" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/shares/{share_id}" in schema["paths"]
    assert "/travel/v2/shares/{share_id}" in schema["paths"]
    assert "/travel/v2/shares/{share_id}/export.ics" in schema["paths"]
    assert "/travel/v2/trips/{trip_id}/revisions/{revision}/export.ics" in schema[
        "paths"
    ]
    share_create = schema["paths"]["/travel/v2/trips/{trip_id}/shares"]["post"]
    idempotency_header = next(
        parameter
        for parameter in share_create["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency_header["in"] == "header"
    assert idempotency_header["required"] is True


def test_trip_edit_service_initializes_idempotency_store():
    assert TripEditService()._operations is not None


def test_create_persistence_failure_is_503_without_saved_success(monkeypatch):
    def fail_create(self, **_kwargs):
        raise TravelV2PersistenceError("database unavailable")

    monkeypatch.setattr(TripService, "create_trip", fail_create)
    response = _client().post(
        "/travel/v2/trips",
        headers={
            "X-User-Id": "user-a",
            "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a",
            "Idempotency-Key": "failure-test-key",
        },
        json={"title": "杭州一日", "document": valid_document()},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
            "message": "行程没有保存，请稍后重试",
        }
    }
    assert "saved" not in response.json()


def test_trip_read_uses_authenticated_owner_and_returns_arrangements(monkeypatch):
    trip_id = "00000000-0000-4000-8000-000000000073"
    document = valid_document()
    document["arrangements"] = {
        "lodgings": [{
            "merchant": {"name": "西湖酒店", "address": "杭州市西湖区"},
            "check_in": "2026-10-20", "check_out": "2026-10-21",
            "booking_status": "not_booked",
        }],
        "intercity_trains": [{
            "train_code": "G1234", "travel_date": "2026-10-20",
            "departure_station": "上海虹桥", "arrival_station": "杭州东",
            "departure_time": "08:00", "arrival_time": "08:45",
            "booking_status": "not_booked",
        }],
    }
    expected_scope = {"tenant_id": "tenant-a", "owner_id": "user-a"}

    def get_trip(self, requested_trip_id, *, tenant_id, owner_id):
        assert str(requested_trip_id) == trip_id
        if {"tenant_id": tenant_id, "owner_id": owner_id} != expected_scope:
            raise TripNotFound("行程不存在")
        return {
            "trip_id": trip_id, "title": "杭州正式行程", "status": "active",
            "revision": 4, "document": document,
        }

    monkeypatch.setattr(TripService, "get_trip", get_trip)
    client = _client()
    owner_headers = {
        "X-User-Id": "user-a", "X-Auth-Type": "jwt", "X-Tenant-Id": "tenant-a",
    }

    owner_response = client.get(f"/travel/v2/trips/{trip_id}", headers=owner_headers)
    other_user_response = client.get(
        f"/travel/v2/trips/{trip_id}", headers={**owner_headers, "X-User-Id": "user-b"},
    )

    assert owner_response.status_code == 200
    assert owner_response.json()["revision"] == 4
    assert owner_response.json()["document"]["arrangements"]["lodgings"][0]["merchant"]["name"] == "西湖酒店"
    assert owner_response.json()["document"]["arrangements"]["intercity_trains"][0]["train_code"] == "G1234"
    assert other_user_response.status_code == 404


def test_share_and_export_persistence_errors_are_safe_503(monkeypatch):
    database_failure = "postgresql://private-user:secret@db.internal/private"

    def fail(*_args, **_kwargs):
        raise TravelV2PersistenceError(database_failure)

    monkeypatch.setattr(ShareService, "create_share", fail)
    monkeypatch.setattr(ShareService, "get_public_share", fail)
    monkeypatch.setattr(ShareService, "revoke_share", fail)
    monkeypatch.setattr(RevisionExportService, "get_snapshot", fail)
    client = _client()
    identity_headers = {
        "X-User-Id": "user-a",
        "X-Auth-Type": "jwt",
        "X-Tenant-Id": "tenant-a",
    }
    responses = [
        client.post(
            "/travel/v2/trips/00000000-0000-0000-0000-000000000001/shares",
            headers={**identity_headers, "Idempotency-Key": "share-error-1"},
            json={"revision": 1},
        ),
        client.get(
            "/travel/v2/shares/00000000-0000-0000-0000-000000000002",
            headers={"X-Travel-Share-Token": "opaque"},
        ),
        client.delete(
            "/travel/v2/trips/00000000-0000-0000-0000-000000000001/shares/00000000-0000-0000-0000-000000000002",
            headers=identity_headers,
        ),
        client.get(
            "/travel/v2/trips/00000000-0000-0000-0000-000000000001/revisions/1/export.ics",
            headers=identity_headers,
        ),
        client.get(
            "/travel/v2/shares/00000000-0000-0000-0000-000000000002/export.ics",
            headers={"X-Travel-Share-Token": "opaque"},
        ),
    ]

    for response in responses:
        assert response.status_code == 503
        assert "TRAVEL_V2_PERSISTENCE_UNAVAILABLE" in response.text
        assert "postgresql://" not in response.text
        assert "private-user" not in response.text


def test_missing_share_and_revision_remain_404(monkeypatch):
    def missing_share(self, **_kwargs):
        raise ShareNotFoundError("分享不存在")

    def missing_revision(self, **_kwargs):
        raise TripNotFound("行程或指定 Revision 不存在")

    monkeypatch.setattr(ShareService, "get_public_share", missing_share)
    monkeypatch.setattr(RevisionExportService, "get_snapshot", missing_revision)
    client = _client()
    share = client.get(
        "/travel/v2/shares/00000000-0000-0000-0000-000000000002",
        headers={"X-Travel-Share-Token": "opaque"},
    )
    export = client.get(
        "/travel/v2/trips/00000000-0000-0000-0000-000000000001/revisions/1/export.ics",
        headers={
            "X-User-Id": "user-a",
            "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a",
        },
    )

    assert share.status_code == 404
    assert export.status_code == 404


def test_invalid_revision_schedule_remains_422(monkeypatch):
    def get_snapshot(self, **_kwargs):
        return valid_document()

    def invalid_schedule(_snapshot):
        raise ValueError("行程时间无效")

    monkeypatch.setattr(RevisionExportService, "get_snapshot", get_snapshot)
    monkeypatch.setattr(
        "backend.travel_v2.api.exports.trip_document_to_ics", invalid_schedule,
    )
    response = _client().get(
        "/travel/v2/trips/00000000-0000-0000-0000-000000000001/revisions/1/export.ics",
        headers={
            "X-User-Id": "user-a",
            "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TRAVEL_V2_ICS_INVALID_SCHEDULE"


def test_share_idempotency_payload_conflict_is_409(monkeypatch):
    def conflict(self, **_kwargs):
        raise IdempotencyConflict("details must not come from DB")

    monkeypatch.setattr(ShareService, "create_share", conflict)
    response = _client().post(
        "/travel/v2/trips/00000000-0000-0000-0000-000000000001/shares",
        headers={
            "X-User-Id": "user-a",
            "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a",
            "Idempotency-Key": "share-conflict-1",
        },
        json={"revision": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_stale_revision_is_409_version_conflict(monkeypatch):
    def fail_edit(self, **_kwargs):
        raise VersionConflict("expected revision does not match current revision")

    monkeypatch.setattr(TripEditService, "apply_document", fail_edit)
    response = _client().put(
        "/travel/v2/trips/00000000-0000-0000-0000-000000000001/document",
        headers={
            "X-User-Id": "user-a",
            "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a",
            "Idempotency-Key": "stale-revision-test",
        },
        json={
            "expected_revision": 1,
            "command_type": "structured_edit",
            "change_summary": "测试并发冲突",
            "document": valid_document(),
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "VERSION_CONFLICT",
            "message": "expected revision does not match current revision",
        }
    }
    assert "saved" not in response.json()


def test_structured_edit_route_uses_server_identity_and_returns_saved_revision(monkeypatch):
    calls = []

    def apply(self, **kwargs):
        calls.append(kwargs)
        return {"saved": True, "revision": 2, "document": valid_document(), "status": "active"}

    monkeypatch.setattr(TripEditService, "apply_operation", apply)
    response = _client().post(
        "/travel/v2/trips/00000000-0000-0000-0000-000000000001/edits",
        headers={
            "X-User-Id": "user-a", "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a", "Idempotency-Key": "structured-edit-1",
        },
        json={
            "expected_revision": 1,
            "change_summary": "添加休息活动",
            "operation": {
                "op": "add_activity", "day_id": "day-1",
                "activity_type": "rest", "title": "休息", "duration_min": 30,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["owner_id"] == "user-a"
    assert calls[0]["actor_id"] == "user-a"
    assert calls[0]["idempotency_key"] == "structured-edit-1"


def test_full_document_route_keeps_success_receipt_shape(monkeypatch):
    receipt = {
        "saved": True, "trip_id": "00000000-0000-0000-0000-000000000001",
        "revision": 2, "document": valid_document(), "status": "active",
        "replayed": False,
    }
    calls = []

    def apply(self, **kwargs):
        calls.append(kwargs)
        return receipt

    monkeypatch.setattr(TripEditService, "apply_document", apply)
    response = _client().put(
        "/travel/v2/trips/00000000-0000-0000-0000-000000000001/document",
        headers={
            "X-User-Id": "user-a", "X-Auth-Type": "jwt",
            "X-Tenant-Id": "tenant-a", "Idempotency-Key": "document-edit-1",
        },
        json={
            "expected_revision": 1, "command_type": "replace_document",
            "change_summary": "保存文档", "document": valid_document(),
        },
    )

    assert response.status_code == 200
    assert response.json() == receipt
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["owner_id"] == "user-a"
    assert calls[0]["idempotency_key"] == "document-edit-1"


def test_template_write_requires_admin_user():
    response = _client().post(
        "/travel/v2/templates",
        headers={
            "X-User-Id": "user-a",
            "X-Auth-Type": "jwt",
            "X-User-Roles": "viewer",
        },
        json={},
    )

    assert response.status_code == 403
