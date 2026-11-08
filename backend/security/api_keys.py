"""security/api_keys.py — 调用方级 API Key 治理（2026-11-08 安全审计 T9）。

背景：平台原本只有一把共享 ``API_KEY``（env），泄露即全平台、无法按调用方
吊销或归因。本模块提供：

- ``generate_api_key()``：生成 ``ak_<urlsafe>`` 原始 Key（只展示一次）；
- ``hash_api_key()``：SHA-256 摘要入库，明文不落库、不落日志；
- ``verify_api_key()``：按摘要查 ``auth.api_keys``，校验 enabled / 过期 /
  吊销；15s 进程缓存，吊销最迟 15s 生效；DB 异常 fail-closed（拒绝）。

边界：env 里的 ``API_KEY`` 仍是 legacy/引导凭据（由中间件先比对）；本模块
只处理 ``ak_`` 前缀的调用方 Key。Key 只证明“持有服务端签发的调用方凭据”，
不映射真人身份——敏感端点仍由 ``deps.require_user_actor`` 拒绝 service 身份。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time

logger = logging.getLogger(__name__)

KEY_PREFIX = "ak_"
CACHE_TTL_SECONDS = 15.0

_cache: dict[str, tuple[dict | None, float]] = {}
_cache_lock = threading.Lock()


def hash_api_key(raw_key: str) -> str:
    """原始 Key → SHA-256 十六进制摘要（落库/日志指纹的唯一形式）。"""
    return hashlib.sha256((raw_key or "").encode("utf-8")).hexdigest()


def key_fingerprint(raw_key: str) -> str:
    """日志用短指纹（不可反推，够区分调用方）。"""
    return hash_api_key(raw_key)[:8]


def generate_api_key() -> tuple[str, str, str]:
    """生成 (raw_key, key_id, key_hash)；raw_key 只返回一次。"""
    raw = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    key_id = f"{KEY_PREFIX}{secrets.token_hex(6)}"
    return raw, key_id, hash_api_key(raw)


def _connect():
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=3)


def _load_row(raw_key: str) -> dict | None:
    """查库并做生命周期判定；任何异常按未授权处理（fail-closed）。"""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT key_id, caller, tenant_id, scopes, enabled, "
                    "expires_at, revoked_at FROM auth.api_keys "
                    "WHERE key_hash = %s",
                    (hash_api_key(raw_key),),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                key_id, caller, tenant_id, scopes, enabled, expires_at, revoked_at = row
                if not enabled or revoked_at is not None:
                    return None
                if expires_at is not None and expires_at.timestamp() <= time.time():
                    return None
                # 使用痕迹：缓存命中不写库，因此最多每 CACHE_TTL 更新一次
                cur.execute(
                    "UPDATE auth.api_keys SET last_used_at = now() "
                    "WHERE key_id = %s",
                    (key_id,),
                )
                conn.commit()
                return {
                    "key_id": key_id,
                    "caller": caller,
                    "tenant_id": tenant_id,
                    "scopes": tuple(scopes or ()),
                }
    except Exception as exc:  # noqa: BLE001 — 凭据校验失败必须拒绝
        logger.warning("[APIKey] 校验失败（按未授权处理）: %s", type(exc).__name__)
        return None


def verify_api_key(raw_key: str) -> dict | None:
    """校验调用方 Key；返回调用方信息或 None。15s 缓存。"""
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(raw_key)
        if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
            return cached[0]
    record = _load_row(raw_key)
    with _cache_lock:
        _cache[raw_key] = (record, now)
        # 防止缓存无界增长：仅保留最近 1000 个 Key
        if len(_cache) > 1000:
            oldest = sorted(_cache, key=lambda k: _cache[k][1])[:200]
            for key in oldest:
                _cache.pop(key, None)
    if record is not None:
        logger.info(
            "[APIKey] 认证通过 key_id=%s caller=%s fingerprint=%s",
            record["key_id"], record["caller"], key_fingerprint(raw_key),
        )
    return record


def clear_cache() -> None:
    """测试/管理用：清空进程缓存（生产吊销靠 TTL 过期）。"""
    with _cache_lock:
        _cache.clear()
