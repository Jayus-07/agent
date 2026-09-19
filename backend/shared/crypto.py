"""shared/crypto.py — 通用 Fernet 对称加解密原语

供两处使用（语义相反，必须分开，不得混用）：

1. **优雅降级**（`strict=False`）：解密失败返回原文。
   用于竞品 Cookie —— 历史数据里既有明文也有密文，密钥轮换后旧数据只能降级读，
   硬失败会让存量配置全不可用。见 `competitor/crypto.py`。

2. **fail-loud**（`strict=True`，默认）：解密失败抛 `SecretDecryptError`。
   用于**出站** API Key（要发给第三方的那种）。静默返回原文会让密文
   `enc:gAAAA…` 被当成 Key 发出去，换回 `401 Invalid API key`，
   真因（主密钥丢失/轮换）被埋在多层语义错误之下
   （与 `config/llm.py` 记录的「真实原因被埋在 N 层语义错误之下」同一类坑）。

密钥来源：环境变量（默认 `SECRETS_ENCRYPTION_KEY`）。**主密钥必须留在 `.env`** ——
它是解密其他密钥的根，不能存在被自己加密的库里（鸡生蛋）。

`cryptography` 一律函数内延迟导入：本模块处在若干高频导入链上，顶层导入会拖慢
冷启动（与 `infra/llm/providers/*` 的延迟导入同理）。
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

# 出站凭据的主密钥（`.env`）；Cookie 走 COOKIE_ENCRYPTION_KEY
DEFAULT_KEY_ENV = "SECRETS_ENCRYPTION_KEY"

# 密文前缀：用于与历史明文区分（改名会破坏存量数据，勿动）
CIPHER_PREFIX = "enc:"

# key_env → 已成功构造的 Fernet 实例。
# 只缓存成功结果 —— 未配置/非法密钥不缓存，以便部署或测试期间 setenv 后立即生效
# （沿用 competitor/crypto.py 原实现的语义）。
_fernet_cache: dict[str, Any] = {}


class SecretCryptoError(RuntimeError):
    """密钥通道异常基类。"""


class SecretKeyMissing(SecretCryptoError):
    """有密文但主密钥未配置/非法 —— 不得回落明文，必须显式失败。"""


class SecretDecryptError(SecretCryptoError):
    """解密失败（主密钥不对 / 密文截断 / 密钥已轮换）。"""


def _load_key(key_env: str) -> str | None:
    raw = os.getenv(key_env)
    if not raw:
        return None
    return raw.strip()


def get_fernet(key_env: str = DEFAULT_KEY_ENV, *, required: bool = False):
    """按环境变量名取 Fernet 实例（成功构造后进程内缓存）。

    未配置或密钥非法时：`required=True` 抛 `SecretKeyMissing`，否则返回 None。
    """
    cached = _fernet_cache.get(key_env)
    if cached is not None:
        return cached

    key = _load_key(key_env)
    if not key:
        if required:
            raise SecretKeyMissing(
                f"{key_env} 未配置，无法加解密出站凭据（请在 .env 中设置）"
            )
        return None

    try:
        from cryptography.fernet import Fernet

        fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:  # noqa: BLE001 — 缺包与非法密钥同样按"不可用"处理
        if required:
            raise SecretKeyMissing(
                f"{key_env} 不可用（需 cryptography 已安装且密钥为 "
                f"32 字节 urlsafe-base64）：{e}"
            ) from e
        return None

    _fernet_cache[key_env] = fernet
    return fernet


def invalidate_fernet_cache(key_env: str | None = None) -> None:
    """清除 Fernet 缓存（密钥轮换 / 测试注入后调用）。None = 全部清除。"""
    if key_env is None:
        _fernet_cache.clear()
    else:
        _fernet_cache.pop(key_env, None)


def is_encrypted(value: Any) -> bool:
    """值是否为密文（带 `enc:` 前缀的字符串）。"""
    return isinstance(value, str) and value.startswith(CIPHER_PREFIX)


def encrypt_with(fernet, value: str) -> str:
    """用给定 Fernet 加密（带前缀）。fernet 必须非 None（调用方已判空）。"""
    return CIPHER_PREFIX + fernet.encrypt(value.encode()).decode()


def decrypt_with(
    fernet,
    value: str,
    *,
    strict: bool = True,
    key_env: str = DEFAULT_KEY_ENV,
) -> str:
    """解密。非密文原样返回。

    `strict=True`：无密钥 / 解密失败一律抛异常（出站凭据通道，默认）。
    `strict=False`：无密钥 / 解密失败返回**原值**（历史数据降级读，Cookie 用）。
    """
    if not is_encrypted(value):
        return value

    if fernet is None:
        if strict:
            raise SecretKeyMissing(
                f"值为密文但 {key_env} 未配置（或不可用），无法解密。"
                f"若主密钥丢失，请用原值重新写入密钥，勿使用密文原文。"
            )
        return value

    try:
        return fernet.decrypt(value[len(CIPHER_PREFIX):].encode()).decode()
    except Exception as e:  # noqa: BLE001
        if strict:
            raise SecretDecryptError(
                f"解密失败（{key_env} 与密文不匹配，或密文被截断）：{e}"
            ) from e
        return value


def encrypt_secret(value: str, *, key_env: str = DEFAULT_KEY_ENV) -> str:
    """加密出站凭据。**主密钥缺失即抛错** —— 绝不静默落明文。"""
    return encrypt_with(get_fernet(key_env, required=True), value)


def decrypt_secret(
    value: str,
    *,
    key_env: str = DEFAULT_KEY_ENV,
    strict: bool = True,
) -> str:
    """解密出站凭据（默认 fail-loud，见模块 docstring）。"""
    return decrypt_with(
        get_fernet(key_env), value, strict=strict, key_env=key_env
    )


# ── 脱敏展示（审计 / 管理端列表；明文与密文都不得落审计）──────────────


def fingerprint(value: str, *, length: int = 12) -> str:
    """值指纹（sha256 前 N 位十六进制）—— 审计只落它，不落明文与密文。"""
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def last4(value: str) -> str:
    """末 4 位（不足 4 位返回空，避免短密钥被整体暴露）。"""
    if not value or len(value) < 4:
        return ""
    return value[-4:]


def mask_secret(value: str) -> str:
    """脱敏展示串：`····` + 末 4 位。"""
    if not value:
        return ""
    return "····" + last4(value)
