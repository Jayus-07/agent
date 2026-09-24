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


def _record_idempotency_metric(metric_name: str, operation: str, result: str) -> None:
    """记录幂等闭环指标；观测失败不得改变副作用协议。"""
    try:
        from backend.observability import metrics

        getattr(metrics, metric_name).labels(
            operation=operation or "unknown", result=result
        ).inc()
    except Exception as exc:
        logger.warning("[Idempotency] 指标写入失败: %s", exc)


def _key_hash(key: IdempotencyKey) -> str:
    """日志/trace 只落 key 摘要（前 12 位），不落完整业务键。"""
    return hashlib.sha256(key.storage_key().encode("utf-8")).hexdigest()[:12]


def _log_decision(
    event: str,
    *,
    key: IdempotencyKey,
    decision: str,
    owner: str = "",
    reason: str = "",
    duration_ms: float | None = None,
) -> None:
    """结构化决策日志——dedup/conflict/in_doubt 行为可定位（Step6 G17）。"""
    logger.info(
        "[Idempotency] event=%s decision=%s operation=%s key_hash=%s "
        "owner=%s reason=%s duration_ms=%s",
        event, decision, key.operation, _key_hash(key),
        owner or "-", reason or "-",
        f"{duration_ms:.1f}" if duration_ms is not None else "-",
    )


def _trace_idempotency_tags(**attrs: Any) -> None:
    """best-effort 把幂等决策写进当前 trace tags；失败不影响副作用协议。"""
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is None:
            return
        for name, value in attrs.items():
            if value:
                trace.tags[f"idempotency.{name}"] = str(value)
    except Exception as exc:
        logger.debug("[Idempotency] trace 标记失败: %s", exc)


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


class IdempotencyConflict(ValueError):
    """同 key 不同 payload，或幂等状态不确定；必须中止，不得复用旧结果。

    继承 ValueError 保持与既有调用方（捕获 ValueError）的兼容。
    """


class SideEffectOutcomeUnknown(RuntimeError):
    """副作用结果未知（Phase3 STOP C 原语）。

    语义：外部 provider 调用可能已越过副作用边界（请求已发出/可能已被
    接受），但调用方无法给出确定结果——例如 SMTP DATA 阶段连接中断、
    HTTP read-timeout-after-write。executor 捕获本异常时把 ledger 落为
    IDEMPOTENCY_UNCERTAIN（保守阻断），禁止同 key 自动重试；与
    「operation 成功但 complete 前崩溃」的 UNCERTAIN 同一阻断语义。
    上层 provider 契约层（backend/shared/provider_idempotency.py）
    负责在副作用边界处把 UNKNOWN outcome 映射到本异常；
    明确未越过边界的失败（NOT_SENT）不得使用本异常（保持可重试）。
    """


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

    def __init__(
        self,
        store: Any,
        result_store: Any | None = None,
        pre_execute=None,
    ):
        self.store = store
        self.result_store = result_store
        self.pre_execute = pre_execute

    def execute(
        self,
        key: IdempotencyKey,
        payload: Any,
        operation,
    ) -> dict[str, Any]:
        if self.result_store is not None:
            replay = self.result_store.get(key, payload)
            if replay is not None:
                _record_idempotency_metric(
                    "idempotency_claim_total", key.operation, "replay"
                )
                _record_idempotency_metric(
                    "idempotency_execution_total", key.operation, "replay"
                )
                return dict(replay)
        try:
            claim = self.store.claim(key, payload)
        except IdempotencyUnavailable:
            _record_idempotency_metric(
                "idempotency_claim_total", key.operation, "unavailable"
            )
            raise
        _record_idempotency_metric(
            "idempotency_claim_total", key.operation, claim.status.value
        )
        _log_decision(
            "claim", key=key, decision=claim.status.value,
            owner=claim.lease_id[:8] if claim.lease_id else "",
            reason=claim.error_code,
        )
        if claim.status == ClaimStatus.SUCCEEDED:
            _trace_idempotency_tags(decision="replayed", reused="true")
            _record_idempotency_metric(
                "idempotency_execution_total", key.operation, "replay"
            )
            return dict(claim.result or {})
        if claim.status == ClaimStatus.CONFLICT:
            if claim.error_code == _UNCERTAIN_ERROR_CODE:
                _trace_idempotency_tags(decision="in_doubt", conflict="true")
                raise IdempotencyUnavailable(
                    "副作用已执行但幂等终态未知，拒绝再次执行"
                )
            _trace_idempotency_tags(decision="conflict", conflict="true")
            raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
        if claim.status != ClaimStatus.NEW:
            _trace_idempotency_tags(decision="in_progress")
            raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
        result_persisted = False
        operation_succeeded = False
        started = time.monotonic()
        try:
            if self.pre_execute is not None:
                self.pre_execute()
            result = operation()
            if not isinstance(result, dict):
                raise TypeError("幂等副作用结果必须是 dict")
            operation_succeeded = True
            if self.result_store is not None:
                self.result_store.complete(key, payload, result)
                result_persisted = True
            self.store.complete(claim.lease_id, result, key=key)
            _record_idempotency_metric(
                "idempotency_execution_total", key.operation, "success"
            )
            _trace_idempotency_tags(decision="executed", reused="false")
            _log_decision(
                "complete", key=key, decision="success",
                owner=claim.lease_id[:8],
                duration_ms=(time.monotonic() - started) * 1000,
            )
            return result
        except Exception as exc:
            error_code = (
                _UNCERTAIN_ERROR_CODE
                if operation_succeeded and not result_persisted
                else (
                    # Phase3 STOP C：副作用边界后结果未知（UNKNOWN outcome）
                    # 与 crash window 同一保守阻断语义——禁止自动重试
                    _UNCERTAIN_ERROR_CODE
                    if isinstance(exc, SideEffectOutcomeUnknown)
                    else (
                        "UPSTREAM_UNAVAILABLE"
                        if isinstance(exc, IdempotencyUnavailable)
                        else "INTERNAL_ERROR"
                    )
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
                logger.error("[Idempotency] 幂等失败状态回写失败", exc_info=True)
            _record_idempotency_metric(
                "idempotency_execution_total", key.operation,
                "uncertain" if error_code == _UNCERTAIN_ERROR_CODE else "failure",
            )
            _trace_idempotency_tags(
                decision="uncertain" if error_code == _UNCERTAIN_ERROR_CODE
                else "failed",
            )
            _log_decision(
                "complete", key=key,
                decision="uncertain" if error_code == _UNCERTAIN_ERROR_CODE
                else "failure",
                owner=claim.lease_id[:8], reason=error_code,
                duration_ms=(time.monotonic() - started) * 1000,
            )
            raise


class PostgresIdempotencyResultStore:
    """PG 权威终态仓储；表结构由 SQL 迁移（scripts/init_db.py）创建。"""

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

    def get_status(
        self, *, tenant_id: str, actor_id: str, client_key: str
    ) -> dict[str, Any] | None:
        """按可信身份查询幂等安全摘要，不返回副作用结果正文。"""
        try:
            with self._connection_factory() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operation, status, error_code, attempt,
                           created_at, updated_at, expires_at, result
                    FROM ai.idempotency_records
                    WHERE tenant_id = %s AND actor_id = %s AND client_key = %s
                      AND (expires_at IS NULL OR expires_at > now())
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_id, actor_id, client_key),
                )
                row = cur.fetchone()
        except Exception as exc:
            raise IdempotencyUnavailable("PG 幂等状态读取失败") from exc
        if row is None:
            return None

        status = (
            "uncertain"
            if row[2] == _UNCERTAIN_ERROR_CODE
            else str(row[1])
        )
        return {
            "client_key": client_key,
            "operation": str(row[0]),
            "status": status,
            "attempt": int(row[3] or 0),
            "error_code": row[2] or None,
            "has_result": row[7] is not None,
            "created_at": _iso_or_none(row[4]),
            "updated_at": _iso_or_none(row[5]),
            "expires_at": _iso_or_none(row[6]),
        }

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


class PostgresIdempotencyLedgerStore:
    """PG durable ledger 存储——claim/complete/fail 与 Redis store 同一协议。

    副作用幂等的权威事实必须落 PG：Redis 重启/TTL 过期不能把「已发生的
    退款」变成「没发生过」（Step6 §十）。表 = ai.idempotency_records
    （migration 031/047），PK(tenant_id, actor_id, operation, client_key)
    本身就是唯一 claim 闸，INSERT ON CONFLICT DO NOTHING 原子抢注，
    禁止 check-then-act（Step6 §十一）。

    接管（takeover）语义（Step6 §二十，绝不按时间盲目重执行）：
      - status=failed：已确认未产生副作用，允许接管重试；
      - status=running 且租约已过期（crash 窗口，副作用是否发生未知）：
        仅当 takeover_allowed 判定通过才接管；未提供判定时保守阻断
        （按 UNCERTAIN 冲突处理），由 resolve_stale_side_effect 人工裁决。
    """

    _SELECT_COLUMNS = (
        "request_hash, status, error_code, result, lease_id, "
        "(lease_expires_at IS NOT NULL AND lease_expires_at <= now()) "
        "AS lease_expired, owner_execution_id, attempt"
    )

    def __init__(
        self,
        connection_factory=None,
        *,
        table: str = "ai.idempotency_records",
        lease_seconds: int = 300,
        owner_execution_id: str = "",
        takeover_allowed=None,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        self._connection_factory = (
            connection_factory or _default_memory_ledger_connection
        )
        self._table = table
        self.lease_seconds = lease_seconds
        self.owner_execution_id = owner_execution_id
        self._takeover_allowed = takeover_allowed

    def claim(self, key: IdempotencyKey, payload: Any) -> ClaimResult:
        request_hash = canonical_fingerprint(payload)
        lease_id = str(uuid.uuid4())
        try:
            with self._connection_factory() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (
                        tenant_id, actor_id, operation, client_key,
                        request_hash, status, attempt,
                        lease_id, lease_expires_at, owner_execution_id
                    )
                    VALUES (%s, %s, %s, %s, %s, 'running', 1, %s,
                            now() + (%s * interval '1 second'), %s)
                    ON CONFLICT (tenant_id, actor_id, operation, client_key)
                    DO NOTHING
                    RETURNING lease_id
                    """,
                    (
                        key.tenant_id, key.actor_id, key.operation,
                        key.client_key, request_hash, lease_id,
                        self.lease_seconds, self.owner_execution_id or None,
                    ),
                )
                if cur.fetchone() is not None:
                    conn.commit()
                    return ClaimResult(
                        status=ClaimStatus.NEW,
                        lease_id=lease_id,
                        lease_expires_at=time.time() + self.lease_seconds,
                    )
                row = self._select_row(cur, key)
                if row is None:
                    conn.rollback()
                    return ClaimResult(
                        status=ClaimStatus.CONFLICT,
                        error_code="IDEMPOTENCY_CONFLICT",
                    )
                decision = self._decide_claim(cur, conn, key, row, request_hash)
                return decision
        except IdempotencyUnavailable:
            raise
        except Exception as exc:
            raise IdempotencyUnavailable("PG ledger claim 失败") from exc

    def _select_row(self, cur: Any, key: IdempotencyKey) -> tuple | None:
        cur.execute(
            f"""
            SELECT {self._SELECT_COLUMNS}
            FROM {self._table}
            WHERE tenant_id = %s AND actor_id = %s
              AND operation = %s AND client_key = %s
            """,
            (key.tenant_id, key.actor_id, key.operation, key.client_key),
        )
        return cur.fetchone()

    def _decide_claim(
        self,
        cur: Any,
        conn: Any,
        key: IdempotencyKey,
        row: tuple,
        request_hash: str,
    ) -> ClaimResult:
        """按已存在记录的状态决定 NEW/RUNNING/SUCCEEDED/CONFLICT。

        全程在 claim 事务内：接管走条件 UPDATE（WHERE 里复核 failed/租约
        过期），并发抢占由 rowcount 判定，check-then-act 竞态不成立。
        """
        (stored_hash, status, error_code, result, stored_lease,
         lease_expired, owner_execution_id, attempt) = row
        if stored_hash != request_hash:
            conn.rollback()
            return ClaimResult(
                status=ClaimStatus.CONFLICT, error_code="IDEMPOTENCY_CONFLICT",
            )
        if status == ClaimStatus.SUCCEEDED.value:
            conn.rollback()
            return ClaimResult(
                status=ClaimStatus.SUCCEEDED, result=dict(result or {}),
            )
        if (status == ClaimStatus.FAILED.value
                and error_code == _UNCERTAIN_ERROR_CODE):
            conn.rollback()
            return ClaimResult(
                status=ClaimStatus.CONFLICT, error_code=_UNCERTAIN_ERROR_CODE,
            )
        if (status == ClaimStatus.RUNNING.value
                and not lease_expired):
            conn.rollback()
            return ClaimResult(
                status=ClaimStatus.RUNNING, lease_id=str(stored_lease or ""),
            )

        # ── 接管判定：failed = 已确认未执行（可重试）；
        #    running+租约过期 = crash 窗口（需 takeover_allowed 判定）──
        if status == ClaimStatus.RUNNING.value:
            allowed = bool(
                self._takeover_allowed
                and self._takeover_allowed({
                    "owner_execution_id": owner_execution_id or "",
                    "attempt": int(attempt or 0),
                    "error_code": error_code or "",
                })
            )
            if not allowed:
                conn.rollback()
                return ClaimResult(
                    status=ClaimStatus.CONFLICT,
                    error_code=_UNCERTAIN_ERROR_CODE,
                )
        new_lease = str(uuid.uuid4())
        cur.execute(
            f"""
            UPDATE {self._table} SET
                status = 'running',
                lease_id = %s,
                lease_expires_at = now() + (%s * interval '1 second'),
                owner_execution_id = %s,
                error_code = NULL,
                attempt = attempt + 1,
                updated_at = now()
            WHERE tenant_id = %s AND actor_id = %s
              AND operation = %s AND client_key = %s
              AND (status = 'failed'
                   OR (status = 'running'
                       AND lease_expires_at IS NOT NULL
                       AND lease_expires_at <= now()))
            """,
            (
                new_lease, self.lease_seconds, self.owner_execution_id or None,
                key.tenant_id, key.actor_id, key.operation, key.client_key,
            ),
        )
        if cur.rowcount == 1:
            conn.commit()
            return ClaimResult(
                status=ClaimStatus.NEW,
                lease_id=new_lease,
                lease_expires_at=time.time() + self.lease_seconds,
            )
        # 条件 UPDATE 没打中 = 并发被抢先：回滚后按现状重读一次
        conn.rollback()
        with self._connection_factory() as conn2, conn2.cursor() as cur2:
            row2 = self._select_row(cur2, key)
            conn2.rollback()
        if row2 is not None and row2[1] == ClaimStatus.SUCCEEDED.value:
            return ClaimResult(
                status=ClaimStatus.SUCCEEDED, result=dict(row2[3] or {}),
            )
        return ClaimResult(
            status=ClaimStatus.RUNNING,
            lease_id=str(row2[4]) if row2 else "",
        )

    def complete(
        self,
        lease_id: str,
        result: dict[str, Any],
        *,
        key: IdempotencyKey | None = None,
    ) -> None:
        if key is None:
            raise ValueError("PG ledger 完成必须提供 IdempotencyKey")
        if self._finish(key, lease_id, status=ClaimStatus.SUCCEEDED.value,
                        result=result) != 1:
            raise ValueError("幂等 lease 不存在或已失效")

    def complete_in_connection(
        self,
        conn: Any,
        lease_id: str,
        result: dict[str, Any],
        *,
        key: IdempotencyKey,
    ) -> None:
        """与业务写同一事务内写终态（Step6 §二十一 原子模式）；不提交。"""
        if self._finish(key, lease_id, status=ClaimStatus.SUCCEEDED.value,
                        result=result, conn=conn) != 1:
            raise ValueError("幂等 lease 不存在或已失效")

    def fail(
        self,
        lease_id: str,
        error_code: str = "INTERNAL_ERROR",
        *,
        key: IdempotencyKey | None = None,
    ) -> None:
        if key is None:
            raise ValueError("PG ledger 失败收口必须提供 IdempotencyKey")
        if self._finish(key, lease_id, status=ClaimStatus.FAILED.value,
                        error_code=error_code) != 1:
            raise ValueError("幂等 lease 不存在或已失效")

    def _finish(
        self,
        key: IdempotencyKey,
        lease_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        conn: Any = None,
    ) -> int:
        """owner CAS 终态写（Step6 §十九）：只认当前 lease + owner。"""
        owner_clause = ""
        params: list[Any] = [
            status,
            json.dumps(result, ensure_ascii=False, default=str)
            if result is not None else None,
            error_code,
            key.tenant_id, key.actor_id, key.operation, key.client_key,
            lease_id,
        ]
        if self.owner_execution_id:
            # 旧 execution 不得 complete 新 execution 已接管的记录
            owner_clause = " AND owner_execution_id = %s"
            params.append(self.owner_execution_id)
        sql = f"""
            UPDATE {self._table} SET
                status = %s,
                result = %s,
                error_code = %s,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE tenant_id = %s AND actor_id = %s
              AND operation = %s AND client_key = %s
              AND lease_id = %s AND status = 'running'{owner_clause}
        """
        try:
            if conn is not None:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.rowcount
            with self._connection_factory() as owned, owned.cursor() as cur:
                cur.execute(sql, params)
                rowcount = cur.rowcount
                owned.commit()
                return rowcount
        except Exception as exc:
            raise IdempotencyUnavailable("PG ledger 终态写入失败") from exc


def execute_idempotent_in_transaction(
    conn_factory,
    key: IdempotencyKey,
    payload: Any,
    tx_fn,
    *,
    lease_seconds: int = 300,
    owner_execution_id: str = "",
    takeover_allowed=None,
    table: str = "ai.idempotency_records",
) -> dict[str, Any]:
    """数据库内部副作用的原子幂等执行（Step6 §二十一 / G13）。

    tx_fn(conn) 在「业务写 + ledger 终态」同一事务里执行：要么一起提交
    （副作用真实发生一次且标记 SUCCEEDED），要么一起回滚（ledger 标
    FAILED 可安全重试）——不存在的中间态。绝不把外部 HTTP 调用放进来，
    那类入口用 run_idempotent_side_effect。
    """
    store = PostgresIdempotencyLedgerStore(
        conn_factory,
        table=table,
        lease_seconds=lease_seconds,
        owner_execution_id=owner_execution_id,
        takeover_allowed=takeover_allowed,
    )
    claim = store.claim(key, payload)
    _record_idempotency_metric(
        "idempotency_claim_total", key.operation, claim.status.value
    )
    _log_decision(
        "claim", key=key, decision=claim.status.value,
        owner=claim.lease_id[:8] if claim.lease_id else "",
        reason=claim.error_code,
    )
    if claim.status == ClaimStatus.SUCCEEDED:
        _record_idempotency_metric(
            "idempotency_execution_total", key.operation, "replay"
        )
        return dict(claim.result or {})
    if claim.status == ClaimStatus.CONFLICT:
        if claim.error_code == _UNCERTAIN_ERROR_CODE:
            raise IdempotencyUnavailable(
                "副作用已执行但幂等终态未知，拒绝再次执行"
            )
        raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
    if claim.status != ClaimStatus.NEW:
        _record_idempotency_metric(
            "idempotency_execution_total", key.operation, "in_progress"
        )
        raise IdempotencyConflict("IDEMPOTENCY_IN_PROGRESS")

    started = time.monotonic()
    conn = conn_factory()
    try:
        result = tx_fn(conn)
        if not isinstance(result, dict):
            raise TypeError("幂等副作用结果必须是 dict")
        store.complete_in_connection(conn, claim.lease_id, result, key=key)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            logger.warning("[Idempotency] 原子事务回滚失败", exc_info=True)
        error_code = (
            "UPSTREAM_UNAVAILABLE"
            if isinstance(exc, IdempotencyUnavailable)
            else "INTERNAL_ERROR"
        )
        try:
            # 业务写已随事务回滚 = 确认未产生副作用 → FAILED 可安全重试
            store.fail(claim.lease_id, error_code, key=key)
        except Exception:
            logger.error("[Idempotency] 幂等失败状态回写失败", exc_info=True)
        _record_idempotency_metric(
            "idempotency_execution_total", key.operation, "failure"
        )
        _log_decision(
            "complete", key=key, decision="failure",
            owner=claim.lease_id[:8], reason=error_code,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise
    _record_idempotency_metric(
        "idempotency_execution_total", key.operation, "success"
    )
    _log_decision(
        "complete", key=key, decision="success",
        owner=claim.lease_id[:8],
        duration_ms=(time.monotonic() - started) * 1000,
    )
    return result


def resolve_stale_side_effect(
    *,
    tenant_id: str,
    actor_id: str,
    operation: str,
    client_key: str,
    decision: str,
    result: dict[str, Any] | None = None,
    reason: str = "",
    connection_factory=None,
    table: str = "ai.idempotency_records",
) -> bool:
    """人工裁决 stale claim（Step6 §五十三 reconcile 最小实现）。

    只允许处理 status='running' 且租约已过期的记录（绝不碰活跃 claim）：
      decision='executed'     → 副作用已发生：标 SUCCEEDED，后续重放结果
      decision='not_executed' → 副作用未发生：标 FAILED，允许安全重试
    返回是否真的裁决了一条记录。
    """
    if decision not in ("executed", "not_executed"):
        raise ValueError("decision 必须是 executed / not_executed")
    factory = connection_factory or _default_memory_ledger_connection
    executed = decision == "executed"
    resolved_result = result if executed else None
    try:
        with factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table} SET
                    status = %s,
                    result = COALESCE(%s, result),
                    error_code = %s,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE tenant_id = %s AND actor_id = %s
                  AND operation = %s AND client_key = %s
                  AND status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= now()
                """,
                (
                    ClaimStatus.SUCCEEDED.value if executed
                    else ClaimStatus.FAILED.value,
                    json.dumps(resolved_result, ensure_ascii=False,
                               default=str)
                    if resolved_result is not None else None,
                    "MANUAL_RESOLVED_EXECUTED" if executed
                    else "RESOLVED_NOT_EXECUTED",
                    tenant_id, actor_id, operation, client_key,
                ),
            )
            updated = cur.rowcount
            conn.commit()
    except Exception as exc:
        raise IdempotencyUnavailable("PG ledger 裁决失败") from exc
    if updated == 1:
        logger.info(
            "[Idempotency] event=side_effect_reconcile decision=%s "
            "operation=%s reason=%s", decision, operation, reason or "-",
        )
    return updated == 1


def run_idempotent_side_effect(
    operation: str,
    payload: Any,
    fn,
    *,
    tenant_id: str,
    actor_id: str,
    client_key: str,
    owner_execution_id: str = "",
    lease_seconds: int = 300,
    takeover_allowed=None,
    pre_execute=None,
) -> dict[str, Any]:
    """在显式可信身份下执行一次持久化幂等副作用（PG ledger 权威）。

    与 run_idempotent_operation（HTTP 工具语义：Redis claim + PG 终态 +
    副作用预算门禁）不同，本入口面向任务运行时/客服域等无 HTTP 请求
    上下文的副作用边界：claim/complete/fail 全部落 PG——Celery retry、
    recovery、resume、admin retry、重复投递都以同一个
    (tenant, actor, operation, client_key) 认出同一 logical operation，
    真实副作用最多发生一次（Step6 G1/G2）。

    约束：
      - owner_execution_id 只是执行者（每次拾取换发），绝不参与 key；
      - client_key 必须来自稳定业务身份（confirmation_id、task_id 组合等），
        禁止每次执行临时生成（uuid4 当 key = 没有幂等）；
      - fn 内部不要做数据库内部业务写后依赖本函数 complete——那类场景
        用 execute_idempotent_in_transaction 同事务收口。
    """
    if not tenant_id or not actor_id:
        raise IdempotencyContextMissing(
            "缺少可信租户或操作者上下文，拒绝执行副作用"
        )
    key = IdempotencyKey(
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation=operation,
        client_key=client_key,
    )
    store = PostgresIdempotencyLedgerStore(
        lease_seconds=lease_seconds,
        owner_execution_id=owner_execution_id,
        takeover_allowed=takeover_allowed,
    )
    executor = IdempotencyExecutor(store, None, pre_execute=pre_execute)
    return executor.execute(key, payload, fn)


def purge_expired_idempotency_records(
    connection_factory=None, *, table: str = "ai.idempotency_records"
) -> int:
    """删除显式 TTL 已过期的幂等记录（Step6 §五十二 保留策略）。

    只清理 expires_at IS NOT NULL 且已过期的行——业务动作类记录默认不写
    expires_at（永久保留，TTL 到期导致不可逆动作重复执行是最危险路径）；
    stale RUNNING claim 是 IN_DOUBT 裁决对象，绝不在此清理。
    返回删除行数。
    """
    factory = connection_factory or _default_memory_ledger_connection
    try:
        with factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table} "
                "WHERE expires_at IS NOT NULL AND expires_at < now()"
            )
            deleted = cur.rowcount
            conn.commit()
            return deleted
    except Exception as exc:
        raise IdempotencyUnavailable("PG ledger 过期清理失败") from exc


def _default_memory_connection():
    import psycopg
    from backend.config.database import MEMORY_DB_CONFIG

    config = MEMORY_DB_CONFIG
    dsn = (
        f"postgresql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}/{config['dbname']}"
    )
    return psycopg.connect(dsn, autocommit=True)


def _default_memory_ledger_connection():
    import psycopg
    from backend.config.database import MEMORY_DB_CONFIG

    config = MEMORY_DB_CONFIG
    dsn = (
        f"postgresql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}/{config['dbname']}"
    )
    # 默认 autocommit=False：claim/接管/终态都在显式事务里提交
    return psycopg.connect(dsn)


def _decode_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


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
        pre_execute=lambda: _enforce_side_effect_budget(
            user_id=actor_id,
            tenant_id=tenant_id,
        ),
    )
    return executor.execute(key, payload, callback)


def _enforce_side_effect_budget(*, user_id: str, tenant_id: str) -> None:
    from backend.infra.llm.quota import enforce_side_effect_budget

    enforce_side_effect_budget(user_id=user_id, tenant_id=tenant_id)


__all__ = [
    "ClaimResult",
    "ClaimStatus",
    "IdempotencyConflict",
    "IdempotencyContextMissing",
    "IdempotencyKey",
    "IdempotencyExecutor",
    "IdempotencyUnavailable",
    "MemoryIdempotencyStore",
    "PostgresIdempotencyLedgerStore",
    "PostgresIdempotencyResultStore",
    "RedisIdempotencyStore",
    "canonical_fingerprint",
    "execute_idempotent_in_transaction",
    "purge_expired_idempotency_records",
    "resolve_stale_side_effect",
    "run_idempotent_operation",
    "run_idempotent_operation_for_identity",
    "run_idempotent_side_effect",
    "SideEffectOutcomeUnknown",
]
