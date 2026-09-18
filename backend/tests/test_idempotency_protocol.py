"""WP2：幂等协议基础的并发与冲突契约测试。"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.shared.idempotency import (
    ClaimStatus,
    IdempotencyExecutor,
    IdempotencyUnavailable,
    IdempotencyKey,
    MemoryIdempotencyStore,
    PostgresIdempotencyResultStore,
    canonical_fingerprint,
)


def _key(client_key: str = "req-1") -> IdempotencyKey:
    return IdempotencyKey(
        tenant_id="tenant-a",
        actor_id="user-1",
        operation="email.send",
        client_key=client_key,
    )


def test_fingerprint_is_canonical_and_body_sensitive():
    left = {"to": "a@example.com", "body": {"b": 2, "a": 1}}
    right = {"body": {"a": 1, "b": 2}, "to": "a@example.com"}

    assert canonical_fingerprint(left) == canonical_fingerprint(right)
    assert canonical_fingerprint({**right, "to": "b@example.com"}) != canonical_fingerprint(left)


def test_idempotency_key_scope_contains_tenant_actor_operation_and_client_key():
    key = _key()

    assert key.storage_key() == "idempotency:v1:tenant-a:user-1:email.send:req-1"
    assert key.request_hash == ""


def test_twenty_concurrent_claims_have_one_winner():
    store = MemoryIdempotencyStore(lease_seconds=30)
    key = _key()
    payload = {"to": "a@example.com", "body": "hello"}

    def claim_once():
        return store.claim(key, payload)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: claim_once(), range(20)))

    assert sum(result.status == ClaimStatus.NEW for result in results) == 1
    assert all(result.status in {ClaimStatus.NEW, ClaimStatus.RUNNING} for result in results)


def test_same_key_different_payload_is_conflict_without_second_claim():
    store = MemoryIdempotencyStore(lease_seconds=30)
    key = _key()

    first = store.claim(key, {"to": "a@example.com"})
    second = store.claim(key, {"to": "b@example.com"})

    assert first.status == ClaimStatus.NEW
    assert second.status == ClaimStatus.CONFLICT
    assert second.error_code == "IDEMPOTENCY_CONFLICT"


def test_successful_result_is_replayed_and_not_reexecuted():
    store = MemoryIdempotencyStore(lease_seconds=30)
    key = _key()
    payload = {"to": "a@example.com"}

    claim = store.claim(key, payload)
    store.complete(claim.lease_id, {"message_id": "m-1"})
    replay = store.claim(key, payload)

    assert replay.status == ClaimStatus.SUCCEEDED
    assert replay.result == {"message_id": "m-1"}


def test_expired_lease_can_be_reclaimed_with_new_lease():
    store = MemoryIdempotencyStore(lease_seconds=1)
    key = _key()
    payload = {"to": "a@example.com"}

    first = store.claim(key, payload)
    store.expire_leases(now=first.lease_expires_at + 1)
    recovered = store.claim(key, payload)

    assert recovered.status == ClaimStatus.NEW
    assert recovered.lease_id != first.lease_id


def test_executor_runs_side_effect_once_and_replays_result():
    store = MemoryIdempotencyStore(lease_seconds=30)
    executor = IdempotencyExecutor(store)
    key = _key()
    calls = []

    result = executor.execute(
        key,
        {"to": "a@example.com"},
        lambda: calls.append("send") or {"message_id": "m-1"},
    )
    replay = executor.execute(
        key,
        {"to": "a@example.com"},
        lambda: calls.append("duplicate") or {"message_id": "m-2"},
    )

    assert result == replay == {"message_id": "m-1"}
    assert calls == ["send"]


def test_executor_failure_does_not_leave_running_claim():
    store = MemoryIdempotencyStore(lease_seconds=30)
    executor = IdempotencyExecutor(store)
    key = _key()

    with pytest.raises(RuntimeError, match="smtp down"):
        executor.execute(
            key,
            {"to": "a@example.com"},
            lambda: (_ for _ in ()).throw(RuntimeError("smtp down")),
        )

    assert store.claim(key, {"to": "a@example.com"}).status == ClaimStatus.NEW


class _UnavailableResultStore:
    """模拟副作用完成后 PG 终态不可写。"""

    def get(self, _key, _payload):
        return None

    def complete(self, _key, _payload, _result):
        raise IdempotencyUnavailable("PG down")

    def fail(self, _key, _payload, _error_code="INTERNAL_ERROR"):
        raise IdempotencyUnavailable("PG down")


def test_result_persist_failure_blocks_reexecution_after_side_effect():
    """副作用成功但 PG 终态未知时，后续请求不得再次执行副作用。"""
    store = MemoryIdempotencyStore(lease_seconds=30)
    executor = IdempotencyExecutor(store, _UnavailableResultStore())
    key = _key("pg-down-after-effect")
    calls = []

    with pytest.raises(IdempotencyUnavailable):
        executor.execute(
            key,
            {"to": "a@example.com"},
            lambda: calls.append("send") or {"message_id": "m-1"},
        )

    with pytest.raises(IdempotencyUnavailable):
        executor.execute(
            key,
            {"to": "a@example.com"},
            lambda: calls.append("duplicate") or {"message_id": "m-2"},
        )

    assert calls == ["send"]


class _FakeCursor:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        if sql.lstrip().startswith("SELECT"):
            self.database.selected = self.database.row
            return
        self.database.row = (
            params[4],
            params[5],
            __import__("json").loads(params[6]) if params[6] else None,
        )

    def fetchone(self):
        return self.database.selected


class _FakeConnection:
    def __init__(self):
        self.row = None
        self.selected = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _FakeCursor(self)


def test_pg_result_store_replays_success_and_rejects_different_payload():
    connection = _FakeConnection()
    store = PostgresIdempotencyResultStore(lambda: connection)
    key = _key()

    store.complete(key, {"to": "a@example.com"}, {"message_id": "m-1"})

    assert store.get(key, {"to": "a@example.com"}) == {"message_id": "m-1"}
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        store.get(key, {"to": "b@example.com"})


def test_invalid_key_parts_are_rejected():
    with pytest.raises(ValueError):
        IdempotencyKey("", "user-1", "email.send", "req-1")
