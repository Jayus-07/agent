"""旅游独立入口的有界执行、协作取消和真实执行寿命管理。

运行对象只通过 ContextVar 传播，绝不写进 checkpoint。取消不能强杀线程；
被跟踪的子调用结束前不释放许可，避免超时重试累积孤儿调用。
"""
from __future__ import annotations

import contextvars
import hashlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar
from uuid import uuid4

from prometheus_client import Counter, Gauge, Histogram

_T = TypeVar("_T")
_control: contextvars.ContextVar[RunControl | None] = contextvars.ContextVar(
    "travel_run_control", default=None,
)
ACTIVE = Gauge("travel_request_active", "旅游真实执行中的请求")
REJECTED = Counter("travel_request_rejected_total", "旅游入口拒绝", ["reason"])
STOPPED = Counter("travel_request_stopped_total", "旅游取消与超时", ["reason"])
DURATION = Histogram("travel_request_duration_seconds", "旅游实际执行寿命")


class RequestRejected(Exception):
    def __init__(self, status_code: int, reason: str):
        self.status_code = status_code
        self.reason = reason
        REJECTED.labels(reason).inc()
        super().__init__(reason)


class RunStopped(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class RunControl:
    def __init__(self, timeout_s: float):
        self.deadline = time.monotonic() + timeout_s
        self.stopped = threading.Event()
        self.reason = ""
        self._condition = threading.Condition()
        self._children = 0

    def cancel(self, reason: str = "cancelled") -> None:
        with self._condition:
            if not self.stopped.is_set():
                self.reason = reason
                self.stopped.set()
                STOPPED.labels(reason).inc()

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            self.cancel("timeout")
        if self.stopped.is_set():
            raise RunStopped(self.reason)

    @contextmanager
    def child(self) -> Iterator[None]:
        with self._condition:
            self._children += 1
        try:
            self.check()
            yield
        finally:
            with self._condition:
                self._children -= 1
                self._condition.notify_all()

    def drain(self) -> None:
        with self._condition:
            self._condition.wait_for(lambda: self._children == 0)


def check_run() -> None:
    control = _control.get()
    if control is not None:
        control.check()


def remaining_budget(maximum: float) -> float:
    check_run()
    control = _control.get()
    return min(maximum, control.deadline - time.monotonic()) if control else maximum


@contextmanager
def child_execution() -> Iterator[None]:
    control = _control.get()
    if control is None:
        yield
    else:
        with control.child():
            yield


@dataclass
class RunHandle:
    future: Future
    control: RunControl


class RequestExecutor:
    """许可先于 submit；线程池内不积压待执行请求。"""
    def __init__(self, workers: int = 16, timeout_s: float = 60,
                 per_user: int = 2, per_tenant: int = 32,
                 lease_store: Callable | None = None):
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="travel-request")
        self._slots = threading.BoundedSemaphore(workers)
        self._lock = threading.Lock()
        self._sessions: set[tuple[str, str, str]] = set()
        self._users: dict[tuple[str, str], int] = {}
        self._tenants: dict[str, int] = {}
        self.timeout_s = timeout_s
        self.per_user = per_user
        self.per_tenant = per_tenant
        self._lease_store = lease_store

    def submit(self, tenant: str, user: str, conversation: str,
               fn: Callable[[RunControl], _T]) -> RunHandle:
        session = (tenant, user, conversation or uuid4().hex)
        uid = (tenant, user)
        with self._lock:
            if session in self._sessions:
                raise RequestRejected(409, "session_busy")
            if (self._users.get(uid, 0) >= self.per_user
                    or self._tenants.get(tenant, 0) >= self.per_tenant
                    or not self._slots.acquire(blocking=False)):
                raise RequestRejected(429, "capacity")
            self._sessions.add(session)
            self._users[uid] = self._users.get(uid, 0) + 1
            self._tenants[tenant] = self._tenants.get(tenant, 0) + 1

        def release_local() -> None:
            with self._lock:
                self._sessions.remove(session)
                for mapping, key in ((self._users, uid), (self._tenants, tenant)):
                    mapping[key] -= 1
                    if mapping[key] == 0:
                        del mapping[key]
                self._slots.release()

        lease = None
        try:
            if self._lease_store is not None:
                store = self._lease_store()
                if store is None:
                    raise RequestRejected(503, "admission_unavailable")
                lease = store.acquire(tenant, user, session[2])
        except BaseException:
            release_local()
            raise
        control = RunControl(self.timeout_s)
        context = contextvars.copy_context()
        ACTIVE.inc()

        def work():
            started = time.monotonic()
            token = _control.set(control)
            try:
                if lease is not None:
                    lease.start(control)
                control.check()
                return fn(control)
            finally:
                control.drain()
                _control.reset(token)
                if lease is not None:
                    lease.close()
                release_local()
                ACTIVE.dec()
                DURATION.observe(time.monotonic() - started)
        try:
            future = self._pool.submit(context.run, work)
        except BaseException:
            if lease is not None:
                lease.close()
            release_local()
            ACTIVE.dec()
            raise
        return RunHandle(future, control)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


# 四个索引同 Redis hash tag，兼容 Redis Cluster 的原子脚本。
_ACQUIRE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
for i, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  if redis.call('ZCARD', key) >= tonumber(ARGV[i+2]) then return i end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('EXPIRE', key, tonumber(ARGV[2]) * 2)
end
return 0
"""
_RENEW = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
for _, key in ipairs(KEYS) do
  local score = redis.call('ZSCORE', key, ARGV[1])
  if not score or tonumber(score) <= now then return 0 end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('EXPIRE', key, tonumber(ARGV[2]) * 2)
end
return 1
"""
_RELEASE = "for _, key in ipairs(KEYS) do redis.call('ZREM', key, ARGV[1]) end return 1"


class RedisLease:
    def __init__(self, client, keys: list[str], member: str, ttl: int):
        self.client, self.keys, self.member, self.ttl = client, keys, member, ttl
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, control: RunControl) -> None:
        def renew():
            while not self._finished.wait(self.ttl / 3):
                try:
                    ok = self.client.eval(_RENEW, len(self.keys), *self.keys,
                                          self.member, self.ttl)
                except Exception:  # 依赖故障必须关闭执行，不能降成本地放行
                    ok = False
                if not ok:
                    control.cancel("admission_unavailable")
                    break
        self._thread = threading.Thread(target=renew, daemon=True,
                                        name="travel-lease-renew")
        self._thread.start()

    def close(self) -> None:
        self._finished.set()
        try:
            self.client.eval(_RELEASE, len(self.keys), *self.keys, self.member)
        except Exception:  # 租约到期回收；拒绝新增由 acquire 故障关闭保证
            from backend.shared.logger import logger
            logger.warning("[TravelAdmission] 租约释放失败，等待过期回收")


class RedisAdmission:
    def __init__(self, client, global_limit: int = 100,
                 tenant_limit: int = 32, user_limit: int = 2, ttl: int = 30):
        self.client = client
        self.limits = (global_limit, tenant_limit, user_limit, 1)
        self.ttl = ttl

    def acquire(self, tenant: str, user: str, conversation: str) -> RedisLease:
        def digest(*parts):
            import json
            return hashlib.sha256(json.dumps(parts).encode()).hexdigest()
        prefix = "travel:{admission}:"
        keys = [prefix + "global", prefix + digest(tenant),
                prefix + digest(tenant, user), prefix + digest(tenant, user, conversation)]
        member = uuid4().hex
        try:
            result = int(self.client.eval(_ACQUIRE, len(keys), *keys,
                                          member, self.ttl, *self.limits))
        except Exception as exc:
            raise RequestRejected(503, "admission_unavailable") from exc
        if result:
            raise RequestRejected(409 if result == 4 else 429,
                                  "session_busy" if result == 4 else "capacity")
        return RedisLease(self.client, keys, member, self.ttl)


_executor: RequestExecutor | None = None
_executor_lock = threading.Lock()


def get_request_executor() -> RequestExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            from backend.config import travel as config
            lease_store = None
            if config.TRAVEL_ADMISSION_BACKEND == "redis":
                def lease_store():
                    from backend.infra.redis.client import get_redis
                    client = get_redis()
                    return RedisAdmission(
                        client, config.TRAVEL_REQUEST_GLOBAL_LIMIT,
                        config.TRAVEL_REQUEST_TENANT_LIMIT,
                        config.TRAVEL_REQUEST_USER_LIMIT,
                    ) if client is not None else None
            _executor = RequestExecutor(
                workers=config.TRAVEL_REQUEST_WORKERS,
                timeout_s=config.TRAVEL_REQUEST_TIMEOUT_S,
                per_user=config.TRAVEL_REQUEST_USER_LIMIT,
                per_tenant=config.TRAVEL_REQUEST_TENANT_LIMIT,
                lease_store=lease_store,
            )
        return _executor
