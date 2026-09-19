"""shared/crypto.py —— 密钥通道原语（P1a-1）

本文件的核心是**两类语义必须区分开**：
- `strict=False` 优雅降级：Cookie 用，兼容密钥轮换前的旧数据
- `strict=True` fail-loud：出站 API Key 用 —— 静默返回密文原文会把
  `enc:gAAAA…` 当 Key 发出去，换回 401，真因被埋掉
"""
from __future__ import annotations

import pytest

from backend.shared import crypto

_KEY_ENV = "SECRETS_ENCRYPTION_KEY"


@pytest.fixture(autouse=True)
def _clean_cache():
    crypto.invalidate_fernet_cache()
    yield
    crypto.invalidate_fernet_cache()


def _new_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


# ── 无主密钥 ──────────────────────────────────────────────────────────


def test_encrypt_without_key_is_loud(monkeypatch):
    """无主密钥时加密出站凭据必须报错，绝不静默落明文。"""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt_secret("sk-x")


def test_decrypt_plaintext_passes_through(monkeypatch):
    """非密文原样返回（历史明文数据仍可读）。"""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    assert crypto.decrypt_secret("sk-plain-value") == "sk-plain-value"


def test_decrypt_ciphertext_without_key_is_loud(monkeypatch):
    """有密文却丢了主密钥 → 必须显式失败（这正是要修掉的静默回退）。"""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.decrypt_secret("enc:gAAAAABmxxxx")

    # 对照：Cookie 的优雅降级分支返回原文，与改造前 competitor/crypto.py 一致
    assert crypto.decrypt_with(None, "enc:gAAAAABmxxxx", strict=False) == "enc:gAAAAABmxxxx"


def test_invalid_key_is_treated_as_missing(monkeypatch):
    """非法密钥（非 Fernet 格式）在 required 时也要显式报错，不能崩在 Fernet 里。"""
    monkeypatch.setenv(_KEY_ENV, "not-a-valid-fernet-key")
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt_secret("sk-x")
    assert crypto.get_fernet(_KEY_ENV) is None  # 非 required → 返回 None 不抛


# ── 有主密钥 ──────────────────────────────────────────────────────────


def test_roundtrip_and_prefix(monkeypatch):
    monkeypatch.setenv(_KEY_ENV, _new_key())
    cipher = crypto.encrypt_secret("sk-secret-abcd")
    assert cipher.startswith(crypto.CIPHER_PREFIX)
    assert cipher != "sk-secret-abcd"
    assert crypto.decrypt_secret(cipher) == "sk-secret-abcd"
    assert crypto.is_encrypted(cipher)


def test_wrong_key_is_loud_but_degradable(monkeypatch):
    """密钥轮换后旧密文：严格模式抛错，降级模式返回原文。"""
    monkeypatch.setenv(_KEY_ENV, _new_key())
    cipher = crypto.encrypt_secret("sk-rotate-me")

    crypto.invalidate_fernet_cache()          # 模拟主密钥更换
    monkeypatch.setenv(_KEY_ENV, _new_key())

    with pytest.raises(crypto.SecretDecryptError):
        crypto.decrypt_secret(cipher)
    assert crypto.decrypt_secret(cipher, strict=False) == cipher


def test_fernet_constructed_once_then_cached(monkeypatch):
    monkeypatch.setenv(_KEY_ENV, _new_key())
    first = crypto.get_fernet(_KEY_ENV)
    assert first is not None
    assert crypto.get_fernet(_KEY_ENV) is first   # 成功结果被缓存
    crypto.invalidate_fernet_cache(_KEY_ENV)
    assert crypto.get_fernet(_KEY_ENV) is not first


def test_cache_is_per_key_env(monkeypatch):
    """不同 env 名的密钥互不干扰（Cookie 与出站凭据各用各的）。"""
    monkeypatch.setenv(_KEY_ENV, _new_key())
    monkeypatch.setenv("COOKIE_ENCRYPTION_KEY", _new_key())
    a = crypto.get_fernet(_KEY_ENV)
    b = crypto.get_fernet("COOKIE_ENCRYPTION_KEY")
    assert a is not None and b is not None and a is not b


# ── 脱敏 ──────────────────────────────────────────────────────────────


def test_fingerprint_is_stable_hex():
    fp = crypto.fingerprint("sk-abcdef")
    assert fp == crypto.fingerprint("sk-abcdef")
    assert len(fp) == 12
    assert fp != crypto.fingerprint("sk-abcdeg")
    assert crypto.fingerprint("") == ""
    assert len(crypto.fingerprint("sk-x", length=6)) == 6


def test_last4_and_mask_do_not_expose_short_values():
    assert crypto.last4("sk-abcdef") == "cdef"
    assert crypto.last4("abc") == ""          # 短值整体隐藏，避免等于全文
    assert crypto.last4("") == ""
    assert crypto.mask_secret("sk-abcdef") == "····cdef"
    assert crypto.mask_secret("") == ""


def test_is_encrypted_handles_non_strings():
    assert crypto.is_encrypted("enc:x")
    assert not crypto.is_encrypted("x")
    assert not crypto.is_encrypted(None)
    assert not crypto.is_encrypted(123)
