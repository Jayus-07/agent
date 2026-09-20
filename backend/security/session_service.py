"""认证会话撤销服务。

PostgreSQL 中的 ``auth.sessions`` 与 ``auth.refresh_tokens`` 是会话权威；
Redis 只负责删除 access-token 会话闸键，让已吊销令牌尽快失效。数据库操作
不在本模块提交事务，由调用方把它纳入所属业务事务。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import text

from backend.infra.redis.client import get_redis
from backend.shared.logger import logger


@dataclass(frozen=True)
class SessionRef:
    """一次需要在提交后清理 Redis 的持久会话引用。"""

    session_id: str
    user_id: int | str


def session_index_key(user_id: int | str, session_id: str) -> str:
    """返回会话 jti 索引键。"""
    return f"auth:session_idx:{user_id}:{session_id}"


def access_session_key(user_id: int | str, jti: str) -> str:
    """返回单个 access token 的会话闸键。"""
    return f"auth:session:{user_id}:{jti}"


class SessionService:
    """封装按用户或按会话撤销的数据库/Redis操作。"""

    def __init__(self, redis_getter: Callable[[], Any] | None = None):
        self._redis_getter = redis_getter or get_redis

    @staticmethod
    def session_ref(session_id: str, user_id: int | str) -> SessionRef:
        """构造测试和调用方可复用的会话引用。"""
        return SessionRef(session_id=str(session_id), user_id=user_id)

    async def revoke_user_sessions(
        self,
        db,
        user_id: int | str,
        *,
        reason: str,
    ) -> list[SessionRef]:
        """在当前事务中吊销用户全部未吊销会话及 refresh token family。

        不提交事务，也不吞掉数据库异常。调用方通常在同一事务中继续完成
        角色/档案/审计更新，提交成功后再调用 ``clear_redis_for_sessions``。
        """
        rows = (await db.execute(text(
            "SELECT id, user_id FROM auth.sessions "
            "WHERE user_id = :uid AND revoked_at IS NULL FOR UPDATE"),
            {"uid": user_id})).mappings().all()
        refs = [
            SessionRef(session_id=str(row["id"]), user_id=row["user_id"])
            for row in rows
        ]
        if refs:
            await db.execute(text(
                "UPDATE auth.sessions "
                "SET revoked_at = now(), revoke_reason = :reason "
                "WHERE user_id = :uid AND revoked_at IS NULL"),
                {"uid": user_id, "reason": reason})
        # 兼容 023 之前 session_id 为空的存量 refresh token；平台角色变更
        # 必须让目标用户的全部 refresh family 失效。
        await db.execute(text(
            "UPDATE auth.refresh_tokens "
            "SET revoked = TRUE, revoked_at = now() "
            "WHERE user_id = :uid AND revoked = FALSE"),
            {"uid": user_id})
        return refs

    async def revoke_session(
        self,
        db,
        session_id: str,
        *,
        reason: str,
    ) -> SessionRef | None:
        """在当前事务中吊销一个会话及其 refresh token family。"""
        row = (await db.execute(text(
            "SELECT user_id FROM auth.sessions "
            "WHERE id = :sid FOR UPDATE"),
            {"sid": str(session_id)})).mappings().first()
        if row is None:
            return None

        await db.execute(text(
            "UPDATE auth.sessions "
            "SET revoked_at = COALESCE(revoked_at, now()), "
            "    revoke_reason = CASE WHEN revoked_at IS NULL "
            "                         THEN :reason ELSE revoke_reason END "
            "WHERE id = :sid"),
            {"sid": str(session_id), "reason": reason})
        await db.execute(text(
            "UPDATE auth.refresh_tokens "
            "SET revoked = TRUE, revoked_at = now() "
            "WHERE session_id = :sid AND revoked = FALSE"),
            {"sid": str(session_id)})
        return SessionRef(session_id=str(session_id), user_id=row["user_id"])

    def clear_redis_for_sessions(self, refs: Iterable[SessionRef]) -> int:
        """提交后删除会话闸键；Redis 故障只告警，不回滚数据库结果。"""
        refs = list(refs)
        if not refs:
            return 0

        try:
            client = self._redis_getter()
        except Exception:
            logger.warning(
                "[SessionService] Redis 不可用，会话闸键清理跳过",
                exc_info=True,
            )
            return 0
        if client is None:
            logger.warning("[SessionService] Redis 不可用，会话闸键清理跳过")
            return 0

        deleted = 0
        try:
            for ref in refs:
                index_key = session_index_key(ref.user_id, ref.session_id)
                members = client.smembers(index_key) or set()
                for jti in members:
                    if isinstance(jti, bytes):
                        jti = jti.decode("utf-8")
                    deleted += int(
                        client.delete(access_session_key(ref.user_id, str(jti))) or 0
                    )
                client.delete(index_key)
        except Exception:
            logger.warning(
                "[SessionService] Redis 会话闸键清理异常，数据库吊销仍然有效",
                exc_info=True,
            )
        return deleted

    def clear_redis_for_session(self, ref: SessionRef) -> int:
        """单会话 Redis 清理便捷方法。"""
        return self.clear_redis_for_sessions([ref])


async def revoke_user_sessions(db, user_id: int | str, *, reason: str) -> list[SessionRef]:
    """使用当前 Redis 配置撤销用户全部会话。"""
    return await SessionService().revoke_user_sessions(db, user_id, reason=reason)


async def revoke_session(db, session_id: str, *, reason: str) -> SessionRef | None:
    """使用当前 Redis 配置撤销一个会话。"""
    return await SessionService().revoke_session(db, session_id, reason=reason)


def clear_redis_for_sessions(refs: Iterable[SessionRef]) -> int:
    """使用当前 Redis 配置清理多个会话闸键。"""
    return SessionService().clear_redis_for_sessions(refs)


__all__ = [
    "SessionRef",
    "SessionService",
    "access_session_key",
    "clear_redis_for_sessions",
    "revoke_session",
    "revoke_user_sessions",
    "session_index_key",
]
