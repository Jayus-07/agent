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
import re
import secrets
import threading
import time

logger = logging.getLogger(__name__)

KEY_PREFIX = "ak_"
CACHE_TTL_SECONDS = 15.0

# generate_api_key 是唯一签发口：raw = "ak_" + token_urlsafe(32)（恰好 43 个
# [A-Za-z0-9_-]）。形状不合的 key 直接拒绝，不打 DB（DoS 防线一）。
_KEY_SHAPE_RE = re.compile(r"^ak_[A-Za-z0-9_-]{43}$")

_cache: dict[str, tuple[dict | None, float]] = {}
_cache_lock = threading.Lock()

# 未知 Key 负缓存（评审 P2 修复，2026-11-10）：查库确认不存在的 key 也
# 记住，TTL 内不重复打 DB；独立小容量上限——占满后新未知 key 直接拒绝
# （fail-closed），防止高频随机 ak_ 借逐次 DB 查询放大负载、并挤占正缓存
# 里的合法条目（两条缓存的容量互相隔离，攻击者无法驱逐合法 key）。
NEG_CACHE_MAX = 500
_neg_cache: dict[str, float] = {}


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
    """校验调用方 Key；返回调用方信息或 None。正缓存 15s，负缓存 15s + 容量上限。"""
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(raw_key)
        if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
            return cached[0]
        # 形状快筛（DoS 防线一）：generate_api_key 是唯一签发口，形状不合
        # 的 key 不可能是合法凭据，直接拒绝，不占缓存不打 DB。
        if not _KEY_SHAPE_RE.fullmatch(raw_key):
            return None
        # 负缓存命中（DoS 防线二）：TTL 内不重复查库。
        neg_ts = _neg_cache.get(raw_key)
        if neg_ts is not None:
            if now - neg_ts < CACHE_TTL_SECONDS:
                return None
            _neg_cache.pop(raw_key, None)
        # 负缓存已满（DoS 防线三）：先被动清掉 TTL 已过期的条目再判满。
        # 必须在这里清——攻击者的随机 key 不会被再次查找，TTL 过期没有
        # 主动清理时机；不清的话一次洪泛就永久占满容量，新签发合法 key
        # 被无限期拒绝（fail-closed 变成拒绝服务自己人）。
        if len(_neg_cache) >= NEG_CACHE_MAX:
            expired = [k for k, ts in _neg_cache.items()
                       if now - ts >= CACHE_TTL_SECONDS]
            for k in expired:
                _neg_cache.pop(k, None)
            if len(_neg_cache) >= NEG_CACHE_MAX:
                return None
    record = _load_row(raw_key)
    with _cache_lock:
        if record is None:
            _neg_cache[raw_key] = time.monotonic()
        else:
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
        _neg_cache.clear()
