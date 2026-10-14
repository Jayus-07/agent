from __future__ import annotations

import concurrent.futures
import os
import uuid
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.postgres_test_support import (
    assert_isolated_travel_postgres,
)
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.api.router import router

pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires an explicitly selected isolated travel V2 PostgreSQL",
)


@pytest.fixture(autouse=True)
def _isolated_postgres_guard(monkeypatch) -> None:
    assert_isolated_travel_postgres()
    monkeypatch.setenv(
        "TRAVEL_V2_SHARE_TOKEN_SECRET",
        "isolated-test-share-secret-with-at-least-32-bytes",
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
            cursor.execute(
                "DELETE FROM travel_v2.trip_shares WHERE trip_id=%s::uuid",
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


def _create_share(
    client: TestClient,
    trip_id: str,
    headers: dict,
    revision: int = 1,
    idempotency_key: str | None = None,
) -> tuple[dict, str]:
    response = client.post(
        f"/travel/v2/trips/{trip_id}/shares",
        headers={
            **headers,
            "Idempotency-Key": idempotency_key or f"share-{uuid.uuid4().hex}",
        },
        json={"revision": revision, "expires_in_days": 30},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    expected_path = f"/travel/share/{created['share_id']}#token="
    assert created["share_url"].startswith(expected_path)
    token = created["share_url"].split("#token=", 1)[1]
    assert len(token) >= 40
    return created, token


def test_fixed_revision_share_export_and_owner_tenant_permissions(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    owner = f"owner-{uuid.uuid4().hex[:12]}"
    tenant = f"tenant-{uuid.uuid4().hex[:12]}"
    owner_headers = _headers(owner, tenant)
    other_owner_headers = _headers(f"other-{uuid.uuid4().hex[:8]}", tenant)
    other_tenant_headers = _headers(owner, f"other-{uuid.uuid4().hex[:8]}")
    document = valid_document()
    document["days"][0]["items"][0].update(
        {
            "start_time": "09:00",
            "duration_min": 120,
            "note": "私密备注不应分享或导出",
        }
    )
    document["days"][0]["items"][0]["place"]["facts"]["source"] = "provider-secret"
    document.setdefault("arrangements", {"lodgings": [], "intercity_trains": []})
    document["arrangements"]["intercity_trains"].append(
        {
            "selection_id": "train-selection-private-id",
            "train_code": "G123",
            "travel_date": "2026-10-20",
            "departure_station": "苏州北",
            "arrival_station": "杭州东",
            "departure_time": "23:30",
            "arrival_time": "01:00",
            "duration_min": 90,
            "tickets": {"second_class": 2},
            "fares": {},
            "ticket_source": "provider-secret",
            "fare_source": "provider-secret",
            "queried_at": "2026-10-19T08:00:00+08:00",
            "verification": "verified",
            "booking_status": "not_booked",
        }
    )
    created_trip = client.post(
        "/travel/v2/trips",
        headers={
            **owner_headers,
            "Idempotency-Key": f"share-create-{uuid.uuid4().hex}",
        },
        json={"title": "私密行程标题", "document": document},
    )
    assert created_trip.status_code == 201, created_trip.text
    trip_id = created_trip.json()["trip_id"]

    try:
        assert (
            client.post(
                f"/travel/v2/trips/{trip_id}/shares",
                headers={"Idempotency-Key": "unauthenticated-share-create"},
                json={"revision": 1},
            ).status_code
            == 401
        )
        assert (
            client.post(
                f"/travel/v2/trips/{trip_id}/shares",
                headers={
                    **other_owner_headers,
                    "Idempotency-Key": "other-owner-share-create",
                },
                json={"revision": 1},
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/travel/v2/trips/{trip_id}/shares",
                headers={
                    **other_tenant_headers,
                    "Idempotency-Key": "other-tenant-share-create",
                },
                json={"revision": 1},
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/travel/v2/trips/{trip_id}/shares",
                headers={
                    **owner_headers,
                    "Idempotency-Key": "missing-revision-share-create",
                },
                json={"revision": 99},
            ).status_code
            == 404
        )

        share_key = f"share-create-{uuid.uuid4().hex}"
        share, token = _create_share(
            client,
            trip_id,
            owner_headers,
            idempotency_key=share_key,
        )
        share_id = share["share_id"]
        replay = client.post(
            f"/travel/v2/trips/{trip_id}/shares",
            headers={**owner_headers, "Idempotency-Key": share_key},
            json={"revision": 1, "expires_in_days": 30},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == share
        monkeypatch.setenv(
            "TRAVEL_V2_SHARE_TOKEN_SECRET",
            "rotated-test-share-secret-with-at-least-32-bytes",
        )
        rotated_replay = client.post(
            f"/travel/v2/trips/{trip_id}/shares",
            headers={**owner_headers, "Idempotency-Key": share_key},
            json={"revision": 1, "expires_in_days": 30},
        )
        assert rotated_replay.status_code == 503
        assert rotated_replay.json()["detail"]["code"] == "TRAVEL_V2_SHARE_UNAVAILABLE"
        monkeypatch.setenv(
            "TRAVEL_V2_SHARE_TOKEN_SECRET",
            "isolated-test-share-secret-with-at-least-32-bytes",
        )
        conflict = client.post(
            f"/travel/v2/trips/{trip_id}/shares",
            headers={**owner_headers, "Idempotency-Key": share_key},
            json={"revision": 1, "expires_in_days": 31},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

        concurrent_key = f"share-race-{uuid.uuid4().hex}"

        def create_racing_share(_index: int):
            return client.post(
                f"/travel/v2/trips/{trip_id}/shares",
                headers={**owner_headers, "Idempotency-Key": concurrent_key},
                json={"revision": 1, "expires_in_days": 30},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            raced = list(pool.map(create_racing_share, range(2)))
        assert {response.status_code for response in raced} == {200, 201}
        assert raced[0].json() == raced[1].json()
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) FROM travel_v2.trip_shares
                         WHERE trip_id=%s::uuid AND idempotency_key_sha256 IS NOT NULL""",
                    (trip_id,),
                )
                assert cursor.fetchone()[0] == 2
        finally:
            connection.close()

        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT token_sha256, revision
                         FROM travel_v2.trip_shares WHERE share_id=%s::uuid""",
                    (share_id,),
                )
                token_hash, stored_revision = cursor.fetchone()
            assert token_hash == sha256(token.encode("ascii")).hexdigest()
            assert token not in token_hash
            assert stored_revision == 1
        finally:
            connection.close()
        public_path = f"/travel/v2/shares/{share_id}"
        assert client.get(public_path).status_code == 404
        assert (
            client.get(
                public_path,
                headers={"X-Travel-Share-Token": "invalid-token"},
            ).status_code
            == 404
        )

        shared = client.get(
            public_path,
            headers={"X-Travel-Share-Token": token},
        )
        assert shared.status_code == 200, shared.text
        shared_body = shared.json()
        assert shared.headers["cache-control"] == "no-store, private"
        assert shared.headers["referrer-policy"] == "no-referrer"
        assert shared.headers["x-robots-tag"] == "noindex, nofollow"
        assert shared.headers["vary"] == "X-Travel-Share-Token"
        assert shared_body["revision"] == 1
        assert shared_body["title"] == "杭州"
        assert shared_body["days"][0]["items"][0]["title"] == "西湖"
        # 分享令牌只授权公开分享路由，不能替代正式 Trip 的用户身份。
        private_trip_without_identity = client.get(f"/travel/v2/trips/{trip_id}")
        assert private_trip_without_identity.status_code == 401
        serialized_share = shared.text
        for forbidden in (
            "trip_id",
            "tenant_id",
            "owner_id",
            "actor_id",
            "私密行程标题",
            "私密备注",
            "provider-secret",
            "origin",
            "travelers",
            "budget",
            "trace",
            "chat",
            "train-selection-private-id",
            "tickets",
            "fares",
        ):
            assert forbidden not in serialized_share

        changed = valid_document()
        changed["brief"]["destination"] = "上海"
        changed["days"][0]["title"] = "后续修改"
        edited = client.put(
            f"/travel/v2/trips/{trip_id}/document",
            headers={
                **owner_headers,
                "Idempotency-Key": f"share-edit-{uuid.uuid4().hex}",
            },
            json={
                "expected_revision": 1,
                "command_type": "replace_document",
                "change_summary": "修改行程",
                "document": changed,
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["revision"] == 2

        old_share_again = client.get(
            public_path,
            headers={"X-Travel-Share-Token": token},
        )
        assert old_share_again.status_code == 200
        assert old_share_again.json()["revision"] == 1
        assert old_share_again.json()["title"] == "杭州"
        assert old_share_again.json()["days"][0]["title"] == "湖畔漫游"

        export_old = client.get(
            f"/travel/v2/trips/{trip_id}/revisions/1/export.ics",
            headers=owner_headers,
        )
        assert export_old.status_code == 200, export_old.text
        assert export_old.headers["content-type"].startswith("text/calendar")
        assert (
            "filename*=UTF-8''%E6%9D%AD%E5%B7%9E.ics"
            in export_old.headers["content-disposition"]
        )
        assert "DTSTART;TZID=Asia/Shanghai:20261020T233000" in export_old.text
        assert "DTEND;TZID=Asia/Shanghai:20261021T010000" in export_old.text
        assert "SUMMARY:动车 G123" in export_old.text
        assert "LOCATION:苏州北 → 杭州东" in export_old.text
        assert "到达" not in export_old.text
        assert "私密备注" not in export_old.text
        assert "provider-secret" not in export_old.text
        assert "train-selection-private-id" not in export_old.text

        export_new = client.get(
            f"/travel/v2/trips/{trip_id}/revisions/2/export.ics",
            headers=owner_headers,
        )
        assert export_new.status_code == 200
        assert "DESCRIPTION:杭州；" not in export_new.text
        assert "DESCRIPTION:上海；" in export_new.text

        public_export = client.get(
            f"/travel/v2/shares/{share_id}/export.ics",
            headers={"X-Travel-Share-Token": token},
        )
        assert public_export.status_code == 200
        assert public_export.headers["cache-control"] == "no-store, private"
        assert public_export.headers["vary"] == "X-Travel-Share-Token"
        assert "DTSTART;TZID=Asia/Shanghai:20261020T233000" in public_export.text
        assert "DTEND;TZID=Asia/Shanghai:20261021T010000" in public_export.text

        assert (
            client.delete(
                f"/travel/v2/trips/{trip_id}/shares/{share_id}",
                headers=other_owner_headers,
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/travel/v2/trips/{trip_id}/shares/{share_id}",
                headers=other_tenant_headers,
            ).status_code
            == 404
        )
        assert (
            client.get(
                public_path,
                headers={"X-Travel-Share-Token": token},
            ).status_code
            == 200
        )
        revoked = client.delete(
            f"/travel/v2/trips/{trip_id}/shares/{share_id}",
            headers=owner_headers,
        )
        assert revoked.status_code == 204
        assert (
            client.get(
                public_path,
                headers={"X-Travel-Share-Token": token},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/travel/v2/shares/{share_id}/export.ics",
                headers={"X-Travel-Share-Token": token},
            ).status_code
            == 404
        )

        expiring_share, expiring_token = _create_share(
            client, trip_id, owner_headers, 1
        )
        connection = get_memory_engine().raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE travel_v2.trip_shares
                          SET created_at = now() - interval '2 days',
                              expires_at = now() - interval '1 day'
                        WHERE share_id = %s::uuid""",
                    (expiring_share["share_id"],),
                )
            connection.commit()
        finally:
            connection.close()
        assert (
            client.get(
                f"/travel/v2/shares/{expiring_share['share_id']}",
                headers={"X-Travel-Share-Token": expiring_token},
            ).status_code
            == 404
        )
    finally:
        _cleanup(trip_id)
