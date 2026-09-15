"""tests/security/test_local_jwt.py — 自建认证模块单测（拆分后替代 Java auth）。"""
import pytest

from backend.security.local_jwt import (
    LocalJwtError,
    hash_password,
    issue_access_token,
    token_ttl_seconds,
    verify_access_token,
    verify_password,
)


def test_issue_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    out = issue_access_token(user_id=7, username="u1", dept="d", device_id="dev",
                             roles=["editor"])
    payload = verify_access_token(out["token"])
    assert payload is not None
    assert payload["userId"] == 7
    assert payload["username"] == "u1"
    assert payload["roles"] == ["editor"]
    assert payload["iss"] == "agent-platform"
    assert payload["type"] == "access"


def test_verify_rejects_tampered_signature(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    out = issue_access_token(user_id=1, username="u")
    bad = out["token"][:-6] + "AAAAAA"
    assert verify_access_token(bad) is None


def test_verify_rejects_expired(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    out = issue_access_token(user_id=1, username="u", ttl_seconds=-60)
    assert verify_access_token(out["token"]) is None
    assert token_ttl_seconds(out["token"]) == 0


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(LocalJwtError):
        issue_access_token(user_id=1, username="u")


def test_password_hash_roundtrip():
    stored = hash_password("s3cret-PW")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-PW", stored)
    assert not verify_password("wrong", stored)
    assert hash_password("s3cret-PW") != stored  # 盐随机
