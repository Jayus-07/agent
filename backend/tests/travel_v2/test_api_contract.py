from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.travel_v2.api.router import router
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.services.trip_edit_service import TripEditService, VersionConflict
from backend.travel_v2.services.trip_service import TripService
from backend.tests.travel_v2.test_trip_document import valid_document


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
