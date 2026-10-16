from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.postgres_test_support import assert_isolated_travel_postgres
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.api.router import router

pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires an explicitly selected isolated travel V2 PostgreSQL",
)


@pytest.fixture(autouse=True)
def _isolated_postgres_guard() -> None:
    assert_isolated_travel_postgres()


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
            cursor.execute("DELETE FROM travel_v2.edit_operations WHERE trip_id=%s::uuid", (trip_id,))
            cursor.execute("DELETE FROM travel_v2.trip_revisions WHERE trip_id=%s::uuid", (trip_id,))
            cursor.execute("DELETE FROM travel_v2.trips WHERE trip_id=%s::uuid", (trip_id,))
        connection.commit()
    finally:
        connection.close()


def test_api_creates_reads_and_archives_only_within_identity_scope() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    owner = f"owner-{uuid.uuid4().hex[:12]}"
    tenant = f"tenant-{uuid.uuid4().hex[:12]}"
    key = f"create-{uuid.uuid4().hex}"
    body = {
        "title": "杭州一日", "document": valid_document(),
        "conversation_id": f"travel-{uuid.uuid4().hex}",
    }

    denied = client.get("/travel/v2/trips")
    assert denied.status_code == 401

    created = client.post(
        "/travel/v2/trips", headers={**_headers(owner, tenant), "Idempotency-Key": key},
        json=body,
    )
    assert created.status_code == 201
    trip_id = created.json()["trip_id"]
    try:
        replay = client.post(
            "/travel/v2/trips", headers={**_headers(owner, tenant), "Idempotency-Key": key},
            json=body,
        )
        assert replay.status_code == 200
        assert replay.json()["trip_id"] == trip_id
        assert replay.json()["revision"] == 1
        assert replay.json()["conversation_id"] == body["conversation_id"]
        association = client.get(
            f"/travel/v2/trips/{trip_id}/conversation",
            headers=_headers(owner, tenant),
        )
        assert association.status_code == 200
        assert association.json() == {
            "trip_id": trip_id,
            "conversation_id": body["conversation_id"],
            "linked": True,
        }
        assert client.get(
            f"/travel/v2/trips/{trip_id}/conversation",
            headers=_headers("someone-else", tenant),
        ).status_code == 404
        assert client.get(
            f"/travel/v2/trips/{trip_id}/conversation",
            headers=_headers(owner, "another-tenant"),
        ).status_code == 404
        changed_conversation = {**body, "conversation_id": f"travel-{uuid.uuid4().hex}"}
        conflict = client.post(
            "/travel/v2/trips", headers={**_headers(owner, tenant), "Idempotency-Key": key},
            json=changed_conversation,
        )
        assert conflict.status_code == 409
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
        unlinked = client.get(
            f"/travel/v2/trips/{trip_id}/conversation",
            headers=_headers(owner, tenant),
        )
        assert unlinked.status_code == 200
        assert unlinked.json() == {
            "trip_id": trip_id, "conversation_id": None, "linked": False,
        }

        edited_document = valid_document()
        edited_document["days"][0]["title"] = "权威回执行程"
        edit_headers = {
            **_headers(owner, tenant), "Idempotency-Key": f"edit-{uuid.uuid4().hex}",
        }
        edited = client.put(
            f"/travel/v2/trips/{trip_id}/document",
            headers=edit_headers,
            json={
                "expected_revision": 1, "command_type": "replace_document",
                "change_summary": "更新第一天标题", "document": edited_document,
            },
        )
        assert edited.status_code == 200
        assert edited.json()["saved"] is True
        assert edited.json()["revision"] == 2
        assert edited.json()["document"]["days"][0]["title"] == "权威回执行程"

        edit_replay = client.put(
            f"/travel/v2/trips/{trip_id}/document",
            headers=edit_headers,
            json={
                "expected_revision": 1, "command_type": "replace_document",
                "change_summary": "更新第一天标题", "document": edited_document,
            },
        )
        assert edit_replay.status_code == 200
        assert edit_replay.json()["saved"] is True
        assert edit_replay.json()["revision"] == 2
        assert edit_replay.json()["replayed"] is True

        assert client.get(
            f"/travel/v2/trips/{trip_id}", headers=_headers("someone-else", tenant),
        ).status_code == 404
        assert client.get(
            f"/travel/v2/trips/{trip_id}", headers=_headers(owner, "another-tenant"),
        ).status_code == 404
        assert client.get(
            f"/travel/v2/trips/{trip_id}", headers=_headers(owner, tenant),
        ).json()["document"]["schema_version"] == 2

        archived = client.delete(
            f"/travel/v2/trips/{trip_id}", headers=_headers(owner, tenant),
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        assert client.get(
            f"/travel/v2/trips/{trip_id}", headers=_headers(owner, tenant),
        ).status_code == 404
    finally:
        _cleanup(trip_id)
