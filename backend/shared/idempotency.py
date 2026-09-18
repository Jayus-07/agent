"""全局幂等协议基础。

本模块只定义稳定的键、请求指纹和 claim 状态语义。业务入口通过存储适配器
接入 Redis/PG；测试使用内存实现验证并发和 lease 恢复，不把副作用逻辑塞进这里。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import quote

from backend.shared.logger import logger

_UNCERTAIN_ERROR_CODE = "IDEMPOTENCY_UNCERTAIN"


def canonical_fingerprint(payload: Any) -> str:
    """对请求体做稳定 JSON 规范化并计算 SHA-256。"""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdempotencyKey:
    """幂等记录的最小隔离范围。"""

    tenant_id: str
    actor_id: str
    operation: str
    client_key: str
    request_hash: str = ""

    def __post_init__(self) -> None:
        for name in ("tenant_id", "actor_id", "operation", "client_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 不能为空")

    def storage_key(self) -> str:
        """生成 Redis/内存存储键；分段编码避免输入冒号造成碰撞。"""
        parts = (
            self.tenant_id,
            self.actor_id,
            self.operation,
            self.client_key,
        )
        encoded = [quote(part, safe="") for part in parts]
        return "idempotency:v1:" + ":".join(encoded)


class ClaimStatus(str, Enum):
    NEW = "new"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CONFLICT = "conflict"


class IdempotencyUnavailable(RuntimeError):
    """幂等关键存储不可用；调用方必须拒绝执行副作用。"""


class IdempotencyContextMissing(PermissionError):
    """缺少可信租户/操作者上下文；调用方必须拒绝执行副作用。"""


@dataclass(frozen=True)
class ClaimResult:
    status: ClaimStatus
    lease_id: str = ""
    lease_expires_at: float = 0.0
    result: dict[str, Any] | None = None
    error_code: str = ""


@dataclass
class _Record:
    request_hash: str
    status: ClaimStatus
    lease_id: str
    lease_expires_at: float
    result: dict[str, Any] | None = None
    error_code: str = ""


class MemoryIdempotencyStore:
    """进程内测试存储；生产接入不得用它替代 Redis/PG。"""

    def __init__(self, lease_seconds: int = 60):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        self.lease_seconds = lease_seconds
        self._records: dict[str, _Record] = {}
        self._lock = threading.RLock()

    def claim(self, key: IdempotencyKey, payload: Any) -> ClaimResult:
        request_hash = canonical_fingerprint(payload)
        storage_key = key.storage_key()
        now = time.time()
        with self._lock:
            record = self._records.get(storage_key)
            if record is not None and record.request_hash != request_hash:
                return ClaimResult(
                    status=ClaimStatus.CONFLICT,
                    error_code="IDEMPOTENCY_CONFLICT",
                )
            if record is not None and record.status == ClaimStatus.SUCCEEDED:
                return ClaimResult(
                    status=ClaimStatus.SUCCEEDED,
                    result=dict(record.result or {}),
                )
            if (record is not None
                    and record.status == ClaimStatus.FAILED
                    and record.error_code == _UNCERTAIN_ERROR_CODE):
                return ClaimResult(
                    status=ClaimStatus.CONFLICT,
                    error_code=_UNCERTAIN_ERROR_CODE,
                )
            if record is not None and record.status == ClaimStatus.RUNNING:
                if record.lease_expires_at > now:
                    return ClaimResult(
                        status=ClaimStatus.RUNNING,
                        lease_id=record.lease_id,
                        lease_expires_at=record.lease_expires_at,
                    )
            lease_id = str(uuid.uuid4())
            lease_expires_at = now + self.lease_seconds
            self._records[storage_key] = _Record(
                request_hash=request_hash,
                status=ClaimStatus.RUNNING,
                lease_id=lease_id,
                lease_expires_at=lease_expires_at,
            )
            return ClaimResult(
                status=ClaimStatus.NEW,
                lease_id=lease_id,
                lease_expires_at=lease_expires_at,
            )

    def complete(
        self,
        lease_id: str,
        result: dict[str, Any],
        *,
        key: IdempotencyKey | None = None,
    ) -> None:
        with self._lock:
            record = self._find_lease(lease_id)
            if record is None or record.status != ClaimStatus.RUNNING:
                raise ValueError("幂等 lease 不存在或已失效")
            record.status = ClaimStatus.SUCCEEDED
            record.result = dict(result)
            record.lease_expires_at = 0.0

    def fail(
        self,
        lease_id: str,
        error_code: str = "INTERNAL_ERROR",
        *,
        key: IdempotencyKey | None = None,
    ) -> None:
        with self._lock:
            record = self._find_lease(lease_id)
            if record is None or record.status != ClaimStatus.RUNNING:
                raise ValueError("幂等 lease 不存在或已失效")
            record.status = ClaimStatus.FAILED
            record.error_code = error_code
            record.lease_expires_at = 0.0

    def expire_leases(self, now: float | None = None) -> int:
        """把过期 running 记录变为可恢复的 failed，返回处理数量。"""
        current = time.time() if now is None else now
        changed = 0
        with self._lock:
            for record in self._records.values():
                if (record.status == ClaimStatus.RUNNING
                        and record.lease_expires_at <= current):
                    record.status = ClaimStatus.FAILED
                    record.error_code = "INTERNAL_ERROR"
                    record.lease_expires_at = 0.0
                    changed += 1
        return changed

    def _find_lease(self, lease_id: str) -> _Record | None:
        for record in self._records.values():
            if record.lease_id == lease_id:
                return record
        return None


class RedisIdempotencyStore:
    """Redis 原子 claim 存储。

    Redis 只承担短期 claim/lease 和快速结果缓存；最终结果应由 PG 适配器持久化。
    任何 Redis 异常都转换为 IdempotencyUnavailable，禁止调用方静默降级到内存。
    """

    _CLAIM_LUA = """
    local request_hash = redis.call('HGET', KEYS[1], 'request_hash')
    if not request_hash then
        redis.call('HSET', KEYS[1],
            'request_hash', ARGV[1],
            'status', 'running',
            'lease_id', ARGV[2],
            'lease_expires_at', ARGV[3])
        redis.call('EXPIRE', KEYS[1], ARGV[4])
        return {'new', ARGV[2], ARGV[3], ''}
    end
    if request_hash ~= ARGV[1] then
        return {'conflict', '', '0', 'IDEMPOTENCY_CONFLICT'}
    end
    local status = redis.call('HGET', KEYS[1], 'status')
    if status == 'succeeded' then
        return {'succeeded', '', '0', redis.call('HGET', KEYS[1], 'result') or ''}
    end
    if status == 'failed'
       and redis.call('HGET', KEYS[1], 'error_code') == 'IDEMPOTENCY_UNCERTAIN' then
        return {'conflict', '', '0', 'IDEMPOTENCY_UNCERTAIN'}
    end
    local lease_expires_at = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0')
    if status == 'running' and lease_expires_at > tonumber(ARGV[5]) then
        return {'running', redis.call('HGET', KEYS[1], 'lease_id') or '', lease_expires_at, ''}
    end
    redis.call('HSET', KEYS[1],
        'status', 'running',
        'lease_id', ARGV[2],
        'lease_expires_at', ARGV[3],
        'error_code', '')
    redis.call('EXPIRE', KEYS[1], ARGV[4])
    return {'new', ARGV[2], ARGV[3], ''}
    """

    _COMPLETE_LUA = """
    if redis.call('HGET', KEYS[1], 'lease_id') ~= ARGV[1]
       or redis.call('HGET', KEYS[1], 'status') ~= 'running' then
        return 0
    end
    redis.call('HSET', KEYS[1],
        'status', 'succeeded',
        'result', ARGV[2],
        'lease_expires_at', '0')
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return 1
    """

    _FAIL_LUA = """
    if redis.call('HGET', KEYS[1], 'lease_id') ~= ARGV[1]
       or redis.call('HGET', KEYS[1], 'status') ~= 'running' then
        return 0
    end
    redis.call('HSET', KEYS[1],
        'status', 'failed',
        'error_code', ARGV[2],
        'lease_expires_at', '0')
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return 1
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        lease_seconds: int = 60,
        result_ttl_seconds: int = 86400,
    ):
        if redis_client is None:
            raise IdempotencyUnavailable("Redis 客户端不可用")
        if lease_seconds <= 0 or result_ttl_seconds <= 0:
            raise ValueError("lease_seconds/result_ttl_seconds 必须大于 0")
        self.redis = redis_client
        self.lease_seconds = lease_seconds
        self.result_ttl_seconds = result_ttl_seconds

    def claim(self, key: IdempotencyKey, payload: Any) -> ClaimResult:
        request_hash = canonical_fingerprint(payload)
        lease_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        expires_ms = now_ms + self.lease_seconds * 1000
        try:
            raw = self.redis.eval(
                self._CLAIM_LUA,
                1,
                key.storage_key(),
                request_hash,
                lease_id,
                str(expires_ms),
                str(self.lease_seconds),
                str(now_ms),
            )
        except Exception as exc:
            raise IdempotencyUnavailable("Redis claim 失败") from exc
        values = [_decode_redis_value(value) for value in raw]
        status = ClaimStatus(values[0])
        if status == ClaimStatus.CONFLICT:
            return ClaimResult(status=status, error_code=values[3])
        if status == ClaimStatus.SUCCEEDED:
            result = json.loads(values[3]) if values[3] else {}
            return ClaimResult(status=status, result=result)
        return ClaimResult(
            status=status,
            lease_id=values[1],
            lease_expires_at=float(values[2]) / 1000,
        )

    def complete(
        self,
        lease_id: str,
        result: dict[str, Any],
        *,
        key: IdempotencyKey | None = None,
    ) -> None:
        if key is None:
            raise ValueError("Redis 幂等完成必须提供 IdempotencyKey")
        self._update(
            self._COMPLETE_LUA,
            key.storage_key(),
            lease_id,
            json.dumps(result, ensure_ascii=False, default=str),
            str(self.result_ttl_seconds),
        )

    def fail(
        self,
        lease_id: str,
        error_code: str = "INTERNAL_ERROR",
        *,
        key: IdempotencyKey | None = None,
    ) -> None:
        if key is None:
            raise ValueError("Redis 幂等失败收口必须提供 IdempotencyKey")
        self._update(
            self._FAIL_LUA,
            key.storage_key(),
            lease_id,
            error_code,
            str(self.result_ttl_seconds),
        )

    def _update(self, script: str, storage_key: str, *args: str) -> None:
        try:
            updated = self.redis.eval(script, 1, storage_key, *args)
        except Exception as exc:
            raise IdempotencyUnavailable("Redis 幂等状态更新失败") from exc
        if int(updated) != 1:
            raise ValueError("幂等 lease 不存在或已失效")


class IdempotencyExecutor:
    """把 claim、一次副作用执行和终态写回收敛成一个可复用边界。"""

    def __init__(self, store: Any, result_store: Any | None = None):
        self.store = store
        self.result_store = result_store

    def execute(
        self,
        key: IdempotencyKey,
        payload: Any,
        operation,
    ) -> dict[str, Any]:
        if self.result_store is not None:
            replay = self.result_store.get(key, payload)
            if replay is not None:
                return dict(replay)
        claim = self.store.claim(key, payload)
        if claim.status == ClaimStatus.SUCCEEDED:
            return dict(claim.result or {})
        if claim.status == ClaimStatus.CONFLICT:
            if claim.error_code == _UNCERTAIN_ERROR_CODE:
                raise IdempotencyUnavailable(
                    "副作用已执行但幂等终态未知，拒绝再次执行"
                )
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if claim.status != ClaimStatus.NEW:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        result_persisted = False
        operation_succeeded = False
        try:
            result = operation()
            if not isinstance(result, dict):
                raise TypeError("幂等副作用结果必须是 dict")
            operation_succeeded = True
            if self.result_store is not None:
                self.result_store.complete(key, payload, result)
                result_persisted = True
            self.store.complete(claim.lease_id, result, key=key)
            return result
        except Exception as exc:
            error_code = (
                _UNCERTAIN_ERROR_CODE
                if operation_succeeded and not result_persisted
                else (
                    "UPSTREAM_UNAVAILABLE"
                    if isinstance(exc, IdempotencyUnavailable)
                    else "INTERNAL_ERROR"
                )
            )
            # PG 已经写入 succeeded 时，Redis 回写失败不能把权威成功结果
            # 覆盖成 failed；后续请求应直接从 PG 重放，避免重复副作用。
            if self.result_store is not None and not result_persisted:
                try:
                    self.result_store.fail(key, payload, error_code)
                except Exception:
                    logger.error("[Idempotency] PG 失败状态回写失败", exc_info=True)
            try:
                self.store.fail(claim.lease_id, error_code, key=key)
            except Exception:
                logger.error("[Idempotency] Redis 失败状态回写失败", exc_info=True)
            raise


class PostgresIdempotencyResultStore:
    """PG 权威终态仓储；表结构由 Alembic 迁移创建。"""

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or _default_memory_connection

    def get(
        self, key: IdempotencyKey, payload: Any
    ) -> dict[str, Any] | None:
        request_hash = canonical_fingerprint(payload)
        try:
            with self._connection_factory() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT request_hash, status, result
                    FROM ai.idempotency_records
                    WHERE tenant_id = %s AND actor_id = %s
                      AND operation = %s AND client_key = %s
                      AND (expires_at IS NULL OR expires_at > now())
                    """,
                    (key.tenant_id, key.actor_id, key.operation, key.client_key),
                )
                row = cur.fetchone()
        except Exception as exc:
            raise IdempotencyUnavailable("PG 幂等结果读取失败") from exc
        if row is None:
            return None
        if row[0] != request_hash:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if row[1] != ClaimStatus.SUCCEEDED.value:
            return None
        return dict(row[2] or {})

    def complete(
        self, key: IdempotencyKey, payload: Any, result: dict[str, Any]
    ) -> None:
        self._write_terminal(key, payload, ClaimStatus.SUCCEEDED, result, "")

    def fail(
        self, key: IdempotencyKey, payload: Any, error_code: str = "INTERNAL_ERROR"
    ) -> None:
        self._write_terminal(key, payload, ClaimStatus.FAILED, None, error_code)

    def _write_terminal(
        self,
        key: IdempotencyKey,
        payload: Any,
        status: ClaimStatus,
        result: dict[str, Any] | None,
        error_code: str,
    ) -> None:
        try:
            with self._connection_factory() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai.idempotency_records (
                        tenant_id, actor_id, operation, client_key,
                        request_hash, status, result, error_code,
                        attempt, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, now())
                    ON CONFLICT (tenant_id, actor_id, operation, client_key)
                    DO UPDATE SET
                        request_hash = EXCLUDED.request_hash,
                        status = EXCLUDED.status,
                        result = EXCLUDED.result,
                        error_code = EXCLUDED.error_code,
                        attempt = ai.idempotency_records.attempt + 1,
                        updated_at = now()
                    """,
                    (
                        key.tenant_id,
                        key.actor_id,
                        key.operation,
                        key.client_key,
                        canonical_fingerprint(payload),
                        status.value,
                        json.dumps(result, ensure_ascii=False, default=str)
                        if result is not None else None,
                        error_code or None,
                    ),
                )
        except Exception as exc:
            raise IdempotencyUnavailable("PG 幂等结果写入失败") from exc


def _default_memory_connection():
    import psycopg
    from backend.config.database import MEMORY_DB_CONFIG

    config = MEMORY_DB_CONFIG
    dsn = (
        f"postgresql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}/{config['dbname']}"
    )
    return psycopg.connect(dsn, autocommit=True)


def _decode_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def run_idempotent_operation(
    operation: str,
    payload: Any,
    callback,
    *,
    client_key: str = "",
) -> dict[str, Any]:
    """在可信租户上下文中执行一次全局幂等副作用。"""
    from backend.tools.session import get_tool_tenant_id, get_tool_user_id

    return run_idempotent_operation_for_identity(
        operation,
        payload,
        callback,
        tenant_id=get_tool_tenant_id(),
        actor_id=get_tool_user_id(),
        client_key=client_key,
    )


def run_idempotent_operation_for_identity(
    operation: str,
    payload: Any,
    callback,
    *,
    tenant_id: str,
    actor_id: str,
    client_key: str = "",
) -> dict[str, Any]:
    """使用显式可信身份执行一次全局幂等副作用。

    HTTP 路由和 Celery 控制面不一定运行在 Tool 的 bind 上下文中，
    因此不能为了复用幂等逻辑临时修改 ContextVar；这些入口应直接传入
    已由认证边界解析出的 tenant_id/actor_id。
    """
    from backend.infra.redis.client import get_redis

    if not tenant_id or not actor_id:
        raise IdempotencyContextMissing("缺少可信租户或操作者上下文，拒绝执行副作用")
    redis_client = get_redis()
    if redis_client is None:
        raise IdempotencyUnavailable("幂等 Redis 不可用，拒绝执行副作用")
    key = IdempotencyKey(
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation=operation,
        client_key=client_key or canonical_fingerprint(payload),
    )
    executor = IdempotencyExecutor(
        RedisIdempotencyStore(redis_client),
        PostgresIdempotencyResultStore(),
    )
    return executor.execute(key, payload, callback)


__all__ = [
    "ClaimResult",
    "ClaimStatus",
    "IdempotencyContextMissing",
    "IdempotencyKey",
    "IdempotencyExecutor",
    "IdempotencyUnavailable",
    "MemoryIdempotencyStore",
    "PostgresIdempotencyResultStore",
    "RedisIdempotencyStore",
    "canonical_fingerprint",
    "run_idempotent_operation",
    "run_idempotent_operation_for_identity",
]
