"""调用方级 API Key 治理测试（2026-11-08 安全审计 T9）。

覆盖：
- 生成/哈希/指纹：明文不落库、摘要 64 位；
- DB 生命周期：有效放行、未知拒绝、吊销后拒绝、过期拒绝（真实 PG）；
- 中间件：legacy env Key 与 ak_ Key 都可认证，未知 ak_ 401；
- 管理脚本：create → list → revoke 闭环。
"""
from __future__ import annotations

import importlib
import uuid

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config.database import MEMORY_DB_CONFIG
from backend.security import api_keys


def _pg_ready() -> bool:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('auth.api_keys')")
                return cur.fetchone()[0] is not None
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_ready(), reason="auth.api_keys 不可达（PG 或迁移 141 未就绪）"
)


# ── 纯函数 ────────────────────────────────────────────────────

def test_generate_and_hash_shape():
    raw, key_id, key_hash = api_keys.generate_api_key()
    assert raw.startswith("ak_") and len(raw) > 20
    assert key_id.startswith("ak_")
    assert key_hash == api_keys.hash_api_key(raw)
    assert len(key_hash) == 64
    assert raw not in key_hash
    assert api_keys.key_fingerprint(raw) == key_hash[:8]


def test_verify_rejects_non_prefixed_or_empty():
    assert api_keys.verify_api_key("") is None
    assert api_keys.verify_api_key("legacy-env-key") is None


# ── 真实 PG 生命周期 ─────────────────────────────────────────

def _insert_key(*, enabled=True, expired=False):
    raw, key_id, key_hash = api_keys.generate_api_key()
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.api_keys "
                "(key_id, key_hash, caller, enabled, expires_at) "
                "VALUES (%s, %s, %s, %s, "
                "CASE WHEN %s THEN now() - interval '1 hour' ELSE NULL END)",
                (key_id, key_hash, "test-caller", enabled, expired),
            )
        conn.commit()
    return raw, key_id


def _delete_key(key_id: str) -> None:
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM auth.api_keys WHERE key_id = %s", (key_id,))
        conn.commit()


@requires_pg
def test_verify_active_key():
    raw, key_id = _insert_key()
    try:
        api_keys.clear_cache()
        record = api_keys.verify_api_key(raw)
        assert record is not None
        assert record["key_id"] == key_id
        assert record["caller"] == "test-caller"
    finally:
        _delete_key(key_id)
        api_keys.clear_cache()


@requires_pg
def test_revoked_and_expired_keys_rejected():
    raw, key_id = _insert_key(enabled=False)
    raw_expired, expired_id = _insert_key(expired=True)
    try:
        api_keys.clear_cache()
        assert api_keys.verify_api_key(raw) is None
        assert api_keys.verify_api_key(raw_expired) is None
    finally:
        _delete_key(key_id)
        _delete_key(expired_id)
        api_keys.clear_cache()


@requires_pg
def test_revoke_takes_effect_after_cache_clear():
    raw, key_id = _insert_key()
    try:
        api_keys.clear_cache()
        assert api_keys.verify_api_key(raw) is not None
        with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.api_keys SET enabled = FALSE, revoked_at = now() "
                    "WHERE key_id = %s",
                    (key_id,),
                )
            conn.commit()
        # 缓存未过期前仍放行（15s 传播窗口），清缓存后拒绝
        assert api_keys.verify_api_key(raw) is not None
        api_keys.clear_cache()
        assert api_keys.verify_api_key(raw) is None
    finally:
        _delete_key(key_id)
        api_keys.clear_cache()


@requires_pg
def test_manage_script_create_list_revoke(capsys):
    from backend.scripts import manage_api_keys as script

    caller = f"script-{uuid.uuid4().hex[:6]}"
    assert script.main(["create", "--caller", caller]) == 0
    out = capsys.readouterr().out
    assert "api_key=ak_" in out
    key_id = next(line.split("=", 1)[1] for line in out.splitlines()
                  if line.startswith("key_id="))
    try:
        assert script.main(["list"]) == 0
        assert key_id in capsys.readouterr().out
    finally:
        assert script.main(["revoke", "--key-id", key_id, "--reason", "test"]) == 0
    capsys.readouterr()
    _delete_key(key_id)


# ── 中间件集成 ───────────────────────────────────────────────

@pytest.fixture
def auth_env(monkeypatch):
    def _set(api_key: str = "legacy-key-123"):
        monkeypatch.setenv("API_KEY", api_key)
        monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "false")
        import backend.config as config_mod
        import importlib as il
        il.reload(config_mod)
        import backend.app.api.middleware.auth as auth_mod
        il.reload(auth_mod)
        return auth_mod

    yield _set

    import backend.config as config_mod
    import backend.app.api.middleware.auth as auth_mod
    monkeypatch.setenv("API_KEY", "")
    importlib.reload(config_mod)
    importlib.reload(auth_mod)


def _client() -> TestClient:
    from backend.app.api.middleware.auth import api_key_middleware

    app = FastAPI()

    @app.get("/chat")
    async def chat():
        return {"ok": True}

    app.middleware("http")(api_key_middleware)
    return TestClient(app)


def test_middleware_accepts_legacy_env_key(auth_env):
    auth_env()
    resp = _client().get("/chat", headers={"X-API-Key": "legacy-key-123"})
    assert resp.status_code == 200


def test_middleware_accepts_managed_key(auth_env, monkeypatch):
    auth_env()
    monkeypatch.setattr(
        api_keys, "verify_api_key",
        lambda raw: {"key_id": "ak_test", "caller": "svc", "tenant_id": None,
                     "scopes": ("service",)} if raw == "ak_valid" else None,
    )
    assert _client().get(
        "/chat", headers={"X-API-Key": "ak_valid"}).status_code == 200
    assert _client().get(
        "/chat", headers={"X-API-Key": "ak_invalid"}).status_code == 401
