from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config.database import MEMORY_DB_CONFIG
from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.api.router import router


pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires the explicitly selected local agent_memory PostgreSQL",
)


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
    assert MEMORY_DB_CONFIG["dbname"] == "agent_memory"
    assert MEMORY_DB_CONFIG["port"] == 5433
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    owner = f"owner-{uuid.uuid4().hex[:12]}"
    tenant = f"tenant-{uuid.uuid4().hex[:12]}"
    key = f"create-{uuid.uuid4().hex}"
    body = {"title": "杭州一日", "document": valid_document()}

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
