"""Session 状态存储 — Redis Hash（per-session 对话上下文）。

Key: agent:session:{session_id}
Fields: user_id, created_at, last_active, turn_count, context_json
TTL: 30 分钟（每次访问刷新）

Redis 不可用时 fallback 到进程内 dict（单实例有效）。
"""
from __future__ import annotations

import json
import time
from typing import Any

from backend.shared.logger import logger

_DEFAULT_TTL = 1800  # 30 分钟


class SessionStore:
    """Per-session 对话状态存储。"""

    def __init__(self, ttl: int = _DEFAULT_TTL):
        self._ttl = ttl
        self._local: dict[str, dict] = {}

    def _get_redis(self):
        from backend.infra.redis.client import get_redis
        return get_redis()

    def _key(self, session_id: str) -> str:
        from backend.config.redis import REDIS_KEY_PREFIX
        return f"{REDIS_KEY_PREFIX}session:{session_id}"

    def get(self, session_id: str) -> dict[str, Any] | None:
        """读取 session 状态。返回 None 表示不存在。"""
        r = self._get_redis()
        if r is not None:
            try:
                data = r.hgetall(self._key(session_id))
                if data:
                    r.expire(self._key(session_id), self._ttl)
                    return self._deserialize(data)
                return None
            except Exception as e:
                logger.debug(f"[SessionStore] Redis 读取失败: {e}")

        return self._local.get(session_id)

    def set(self, session_id: str, data: dict[str, Any]) -> None:
        """写入 session 状态。"""
        r = self._get_redis()
        data["last_active"] = str(time.time())
        if r is not None:
            try:
                key = self._key(session_id)
                serialized = self._serialize(data)
                r.hset(key, mapping=serialized)
                r.expire(key, self._ttl)
                return
            except Exception as e:
                logger.debug(f"[SessionStore] Redis 写入失败: {e}")

        self._local[session_id] = data

    def update(self, session_id: str, **fields) -> None:
        """增量更新 session 字段。"""
        r = self._get_redis()
        fields["last_active"] = str(time.time())
        if r is not None:
            try:
                key = self._key(session_id)
                serialized = {k: str(v) for k, v in fields.items()}
                r.hset(key, mapping=serialized)
                r.expire(key, self._ttl)
                return
            except Exception as e:
                logger.debug(f"[SessionStore] Redis 更新失败: {e}")

        local = self._local.setdefault(session_id, {})
        local.update(fields)

    def delete(self, session_id: str) -> None:
        """删除 session。"""
        r = self._get_redis()
        if r is not None:
            try:
                r.delete(self._key(session_id))
            except Exception as e:
                logger.debug(f"[SessionStore] Redis 删除失败: {e}")
        self._local.pop(session_id, None)

    def touch(self, session_id: str) -> None:
        """刷新 TTL（不修改字段）。"""
        r = self._get_redis()
        if r is not None:
            try:
                r.expire(self._key(session_id), self._ttl)
            except Exception:
                pass

    @staticmethod
    def _serialize(data: dict) -> dict[str, str]:
        result = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                result[k] = json.dumps(v, ensure_ascii=False)
            else:
                result[k] = str(v)
        return result

    @staticmethod
    def _deserialize(raw: dict) -> dict[str, Any]:
        result = {}
        for k, v in raw.items():
            if k in ("context_json",):
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            elif k in ("turn_count",):
                try:
                    result[k] = int(v)
                except (ValueError, TypeError):
                    result[k] = 0
            else:
                result[k] = v
        return result


_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """获取 SessionStore 单例。"""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store
