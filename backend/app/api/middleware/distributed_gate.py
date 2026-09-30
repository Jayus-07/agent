"""middleware/distributed_gate.py — 分布式准入门（多副本全局并发硬上限）。

设计（2026-09-30 主架构改造施工方案 P0-1）：
  - Redis sorted set 槽位：member=request_id，score=获取时刻；
    acquire 由 Lua 原子完成「清理过期租约 → 判上限 → 占位」
  - release 在 finally 必达（ZREM 幂等，对不存在 member 不报错）；
    持槽进程崩溃靠 lease TTL（默认 30s）由后续 acquire 顺带回收
  - 分层口径：本门 = 全局硬上限、无等待队列，满即 503 + Retry-After；
    排队与优先级仍由进程内 _PriorityGate（concurrency.py）承担。
    挂载于 concurrency 之前注册（= 执行时在其内层）：请求先拿进程内槽
    再占全局槽，避免持全局槽空等排队
  - fail-open：Redis 不可用 → 放行 + dist_gate_unavailable_total +
    节流 warning（进程内门仍在兜底，可用性优先）
  - 可选租户级上限：按 JWT tenant_id claim 分桶，与全局上限叠加判定
    （判而不占、全部通过才占位，无需回滚）
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.app.api.middleware.path_classes import is_skip_path
from backend.config import (
    DIST_CONCURRENCY_ENABLED,
    DIST_GATE_LEASE_TTL_SECONDS,
    DIST_MAX_CONCURRENT_REQUESTS,
    DIST_TENANT_MAX_CONCURRENT,
)
from backend.observability.metrics import (
    dist_gate_active,
    dist_gate_reject_total,
    dist_gate_unavailable_total,
)
from backend.shared.logger import logger

# KEYS[1]=全局 zset；KEYS[2]=租户 zset（租户上限关闭时脚本不触碰该 key）
# ARGV: 1=全局上限 2=租户上限(0=关) 3=now(秒,float) 4=ttl(毫秒) 5=member
_ACQUIRE_LUA = """
local gkey = KEYS[1]
local tkey = KEYS[2]
local glimit = tonumber(ARGV[1])
local tlimit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])
local member = ARGV[5]
local cutoff = now - ttl_ms / 1000.0

-- 过期租约回收：score 早于 now-ttl 的持有者视为已崩溃，槽位自动归还
redis.call('ZREMRANGEBYSCORE', gkey, '-inf', cutoff)
if redis.call('ZCARD', gkey) >= glimit then
    return {0, 'global_limit'}
end
if tlimit > 0 then
    redis.call('ZREMRANGEBYSCORE', tkey, '-inf', cutoff)
    if redis.call('ZCARD', tkey) >= tlimit then
        return {0, 'tenant_limit'}
    end
end
redis.call('ZADD', gkey, now, member)
redis.call('PEXPIRE', gkey, ttl_ms * 2)
if tlimit > 0 then
    redis.call('ZADD', tkey, now, member)
    redis.call('PEXPIRE', tkey, ttl_ms * 2)
end
return {1, ''}
"""

# fail-open warning 节流：Redis 每次抖动都打会洪泛日志，指标不限流
_WARN_INTERVAL_SECONDS = 60.0


class DistributedGate:
    """Redis sorted-set 全局槽位门（同步实现，middleware 层负责线程化调用）。"""

    def __init__(self) -> None:
        self._sha: str | None = None
        self._last_unavailable_warn: float = 0.0

    # ── 内部工具 ─────────────────────────────────────
    def _redis(self):
        from backend.infra.redis.client import get_redis
        return get_redis()

    def _keys(self, tenant: str) -> tuple[str, str]:
        from backend.config.redis import REDIS_KEY_PREFIX
        prefix = REDIS_KEY_PREFIX or "agent:"
        return (
            f"{prefix}dist_gate:global",
            f"{prefix}dist_gate:tenant:{tenant}",
        )

    def _ensure_script(self, r) -> str:
        if self._sha is not None:
            try:
                if r.script_exists(self._sha)[0]:
                    return self._sha
            except Exception:  # noqa: BLE001 — 校验失败退回重新 load
                pass
        self._sha = r.script_load(_ACQUIRE_LUA)
        return self._sha

    def _record_unavailable(self, cause: str) -> None:
        dist_gate_unavailable_total.inc()
        now = time.monotonic()
        if now - self._last_unavailable_warn >= _WARN_INTERVAL_SECONDS:
            self._last_unavailable_warn = now
            logger.warning(
                "[DistGate] Redis 不可用，fail-open 放行（进程内并发门兜底）: %s",
                cause,
            )

    # ── 对外接口 ─────────────────────────────────────
    def acquire(self, request_id: str, tenant: str = "default") -> tuple[bool, str]:
        """尝试占位。返回 (granted, reason)。

        reason ∈ {'', 'global_limit', 'tenant_limit'}。
        Redis 不可用或调用异常一律放行（fail-open）。
        """
        r = self._redis()
        if r is None:
            self._record_unavailable("redis-not-ready")
            return True, ""
        gkey, tkey = self._keys(tenant)
        try:
            sha = self._ensure_script(r)
            try:
                result = r.evalsha(
                    sha, 2, gkey, tkey,
                    DIST_MAX_CONCURRENT_REQUESTS,
                    DIST_TENANT_MAX_CONCURRENT,
                    time.time(),
                    DIST_GATE_LEASE_TTL_SECONDS * 1000,
                    request_id,
                )
            except Exception as e:  # noqa: BLE001 — SCRIPT FLUSH 后 sha 失效重载一次
                if "NOSCRIPT" not in str(e):
                    raise
                sha = r.script_load(_ACQUIRE_LUA)
                self._sha = sha
                result = r.evalsha(
                    sha, 2, gkey, tkey,
                    DIST_MAX_CONCURRENT_REQUESTS,
                    DIST_TENANT_MAX_CONCURRENT,
                    time.time(),
                    DIST_GATE_LEASE_TTL_SECONDS * 1000,
                    request_id,
                )
            granted = bool(int(result[0]))
            return granted, (result[1] if not granted else "")
        except Exception as e:  # noqa: BLE001 — fail-open：可用性优先
            self._record_unavailable(f"acquire-error: {e}")
            return True, ""

    def release(self, request_id: str, tenant: str = "default") -> None:
        """归还槽位。幂等（ZREM 对不存在 member 返回 0 不报错）；失败仅 debug，
        残留槽位靠 lease TTL 由后续 acquire 回收。"""
        r = self._redis()
        if r is None:
            return
        gkey, tkey = self._keys(tenant)
        try:
            r.zrem(gkey, request_id)
            if DIST_TENANT_MAX_CONCURRENT > 0:
                r.zrem(tkey, request_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("[DistGate] release 失败（槽位将由 lease TTL 回收）: %s", e)


_gate = DistributedGate()


def _tenant_of(request: Request) -> str:
    """读 JWT payload 的 tenant_id 分桶（不验签——验签在网关与内层 auth 中间件，
    此处仅作配额分桶，伪造租户只能改变自己落在哪个桶，不构成提权）。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload_b64 = auth.split(" ", 1)[1].split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            tenant_id = payload.get("tenant_id")
            if tenant_id:
                return str(tenant_id)
        except Exception:  # noqa: BLE001 — 解析失败按 default 桶
            pass
    return "default"


async def distributed_gate_middleware(request: Request, call_next):
    """全局硬上限门：满即 503，不做分布式等待。

    DIST_CONCURRENCY_ENABLED=false 时零 Redis 访问、行为与主线一致。
    """
    if not DIST_CONCURRENCY_ENABLED:
        return await call_next(request)

    # 轻量只读端点与进程内门口径一致：不消耗全局槽位
    if is_skip_path(request.url.path):
        return await call_next(request)

    request_id = f"{uuid.uuid4().hex}:{os.getpid()}"
    tenant = _tenant_of(request)
    # 同步 Redis 调用移出事件循环（socket_timeout 最坏阻塞，不能挂 loop）
    granted, reason = await asyncio.to_thread(_gate.acquire, request_id, tenant)
    if not granted:
        dist_gate_reject_total.labels(reason=reason).inc()
        scope = "租户" if reason == "tenant_limit" else "全局"
        limit = (
            DIST_TENANT_MAX_CONCURRENT
            if reason == "tenant_limit"
            else DIST_MAX_CONCURRENT_REQUESTS
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "ServerBusy",
                "detail": f"{scope}并发已达上限（{limit}），请稍后重试",
            },
            headers={"Retry-After": "2"},
        )

    dist_gate_active.inc()
    try:
        return await call_next(request)
    finally:
        dist_gate_active.dec()
        await asyncio.to_thread(_gate.release, request_id, tenant)
