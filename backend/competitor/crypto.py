"""competitor/crypto.py — Cookie 值加密/解密

使用 Fernet 对称加密保护存储在 DB 中的 Cookie 值。
- COOKIE_ENCRYPTION_KEY 环境变量设置时自动加密
- 未设置时明文存储（开发模式）
- 读取时自动尝试解密，失败则返回明文（向后兼容已有数据）
"""
import os
from typing import Optional

_fernet = None


def _get_fernet():
    """获取 Fernet 实例（单例）。未配置密钥时返回 None。"""
    global _fernet
    if _fernet is not None:
        return _fernet
    key = os.getenv("COOKIE_ENCRYPTION_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None
    return _fernet


def encrypt_cookie(value: str) -> str:
    """加密 Cookie 值。未配置密钥时返回明文。"""
    f = _get_fernet()
    if f is None:
        return value
    return "enc:" + f.encrypt(value.encode()).decode()


def decrypt_cookie(value: str) -> str:
    """解密 Cookie 值。未加密或密钥不匹配时返回原文。"""
    if not value or not value.startswith("enc:"):
        return value
    f = _get_fernet()
    if f is None:
        return value  # 加密值但无密钥 → 返回原文（无法解密）
    try:
        return f.decrypt(value[4:].encode()).decode()
    except Exception:
        return value  # 解密失败 → 可能是密钥更换前的旧数据，返回原文


# 标记哪些 config key 需要加密（旧全局键 + 多平台分键 crawler_cookies:<platform>）
_ENCRYPTED_KEYS = {"crawler_cookies"}


def _is_encrypted_key(key: str) -> bool:
    return key in _ENCRYPTED_KEYS or key.startswith("crawler_cookies:")


def maybe_encrypt(key: str, value: str) -> str:
    """对需要加密的 key 自动加密"""
    if _is_encrypted_key(key):
        return encrypt_cookie(value)
    return value


def maybe_decrypt(key: str, value: Optional[str]) -> Optional[str]:
    """对需要解密的 key 自动解密"""
    if value is None:
        return None
    if _is_encrypted_key(key):
        return decrypt_cookie(value)
    return value
