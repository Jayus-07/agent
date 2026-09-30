"""distributed_gate 单测 — 分布式准入门（P0-1）。

两组用例：
  1) mock 组（不依赖 Redis）：middleware 行为（开关关零访问 / 上限 503+Retry-After /
     放行后 gauge 回零 / skip 路径直通）、fail-open（redis 未就绪 / evalsha 异常）、
     NOSCRIPT 重载重试、release 幂等
  2) 真 Redis 集成组（本地 Redis 不可达整体 skip，db15 隔离，跟随
     test_task_admission_runtime 先例）：Lua 真语义——全局上限与释放恢复、
     过期租约回收（等价 kill 持槽进程后 TTL 兜底）、双门（模拟两副本）共享上限、
     租户级上限叠加判定
"""
from __future__ import annotations

import time

import pytest

import backend.app.api.middleware.distributed_gate as dg
from backend.app.api.middleware.distributed_gate import (
    DistributedGate,
    distributed_gate_middleware,
)

# ─────────────────────────── mock 组 ───────────────────────────


class _FakeGate:
    """脚本可控的假门：直接给定 acquire 结果。"""

    def __init__(self, granted: bool, reason: str = "") -> None:
        self.granted = granted
        self.reason = reason
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self, request_id, tenant="default"):
        self.acquire_calls += 1
        return self.granted, self.reason

    def release(self, request_id, tenant="default"):
        self.release_calls += 1


class _FlakyRedis:
    """只实现 distributed_gate 用到的三个命令；行为由测试注入。"""

    def __init__(self, exc=None, results=None):
        self._exc = exc
        self._results = results if results is not None else [[1, ""]]
        self.script_load_calls = 0

    def script_load(self, src):
        self.script_load_calls += 1
        return "fakesha"

    def script_exists(self, sha):
        return [True]

    def evalsha(self, *args):
        if self._exc is not None:
            raise self._exc
        return self._results.pop(0)


def _make_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/chat/stream")
    async def chat_stream():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "up"}

    app.middleware("http")(distributed_gate_middleware)
    return TestClient(app)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(dg, "DIST_CONCURRENCY_ENABLED", True)


def test_disabled_zero_redis_access(monkeypatch):
    """开关关：直通且零门调用（行为与主线一致的硬保证）。"""
    monkeypatch.setattr(dg, "DIST_CONCURRENCY_ENABLED", False)
    boom = _FakeGate(granted=False)
    monkeypatch.setattr(dg, "_gate", boom)
    with _make_client() as c:
        resp = c.get("/chat/stream")
    assert resp.status_code == 200
    assert boom.acquire_calls == 0


def test_skip_path_bypasses_gate(enabled, monkeypatch):
    """轻量只读端点与进程内门口径一致，不消耗全局槽位。"""
    boom = _FakeGate(granted=False)
    monkeypatch.setattr(dg, "_gate", boom)
    with _make_client() as c:
        resp = c.get("/health")
    assert resp.status_code == 200
    assert boom.acquire_calls == 0


def test_reject_returns_503_with_retry_after(enabled, monkeypatch):
    gate = _FakeGate(granted=False, reason="global_limit")
    monkeypatch.setattr(dg, "_gate", gate)
    before = (
        dg.dist_gate_reject_total.labels(reason="global_limit")._value.get()
    )
    with _make_client() as c:
        resp = c.get("/chat/stream")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "2"
    assert resp.json()["error"] == "ServerBusy"
    assert gate.release_calls == 0  # 未获得槽位不产生 release
    assert (
        dg.dist_gate_reject_total.labels(reason="global_limit")._value.get()
        == before + 1
    )


def test_grant_passes_through_and_gauge_returns_to_zero(enabled, monkeypatch):
    gate = _FakeGate(granted=True)
    monkeypatch.setattr(dg, "_gate", gate)
    with _make_client() as c:
        resp = c.get("/chat/stream")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert gate.acquire_calls == 1
    assert gate.release_calls == 1  # finally 必达
    assert dg.dist_gate_active._value.get() == 0


def test_fail_open_when_redis_not_ready(monkeypatch):
    """get_redis 返回 None：放行 + unavailable 指标。"""
    gate = DistributedGate()
    monkeypatch.setattr(gate, "_redis", lambda: None)
    before = dg.dist_gate_unavailable_total._value.get()
    granted, reason = gate.acquire("req-1", "default")
    assert granted is True and reason == ""
    assert dg.dist_gate_unavailable_total._value.get() == before + 1


def test_fail_open_on_evalsha_error(monkeypatch):
    gate = DistributedGate()
    flaky = _FlakyRedis(exc=ConnectionError("redis down"))
    monkeypatch.setattr(gate, "_redis", lambda: flaky)
    granted, reason = gate.acquire("req-1", "default")
    assert granted is True and reason == ""
    assert dg.dist_gate_unavailable_total._value.get() >= 1


def test_noscript_reload_retry(monkeypatch):
    """SCRIPT FLUSH 后 evalsha 报 NOSCRIPT：重载脚本重试一次后成功。"""
    gate = DistributedGate()
    flaky = _FlakyRedis(results=[[1, ""]])
    calls = {"n": 0}
    orig = flaky.evalsha

    def _first_noscript_then_ok(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("NOSCRIPT No matching script: fakesha")
        return orig(*args)

    flaky.evalsha = _first_noscript_then_ok
    monkeypatch.setattr(gate, "_redis", lambda: flaky)
    granted, _ = gate.acquire("req-1", "default")
    assert granted is True
    assert flaky.script_load_calls == 2  # 初载 + NOSCRIPT 重载
    assert calls["n"] == 2


def test_tenant_of_parses_jwt_payload():
    """租户分桶读 JWT tenant_id claim；缺失/坏 token 回 default。"""
    from fastapi import Request

    def _req(headers: dict) -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in headers.items()
            ],
        }
        return Request(scope)

    # eyJ0ZW5hbnRfaWQiOiJ0MSJ9 = {"tenant_id":"t1"}
    token = "x.eyJ0ZW5hbnRfaWQiOiJ0MSJ9.y"
    assert dg._tenant_of(_req({"authorization": f"Bearer {token}"})) == "t1"
    assert dg._tenant_of(_req({"authorization": "Bearer not.a.jwt"})) == "default"
    assert dg._tenant_of(_req({})) == "default"


# ─────────────────────── 真 Redis 集成组 ───────────────────────


@pytest.fixture(scope="module")
def redis_client():
    try:
        import redis as redis_lib

        from backend.config.redis import REDIS_URL

        # 换 db15（专用测试库）；host 钉死 127.0.0.1 消 DNS 双栈轮换
        base = REDIS_URL.rsplit("/", 1)[0]
        if "@localhost:" in base:
            base = base.replace("@localhost:", "@127.0.0.1:")
        client = redis_lib.Redis.from_url(
            base + "/15", decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        client.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"本地 Redis 不可达，跳过 distributed gate 集成测试: {e}")
    yield client
    try:
        client.flushdb()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def gate(redis_client, monkeypatch):
    """注入测试 Redis + 收紧上限的真实门；每用例清库。"""
    redis_client.flushdb()
    monkeypatch.setattr(dg, "DIST_MAX_CONCURRENT_REQUESTS", 2)
    monkeypatch.setattr(dg, "DIST_TENANT_MAX_CONCURRENT", 0)
    monkeypatch.setattr(dg, "DIST_GATE_LEASE_TTL_SECONDS", 30)
    g = DistributedGate()
    monkeypatch.setattr(g, "_redis", lambda: redis_client)
    return g


def test_global_limit_enforced_and_released(gate, redis_client):
    gkey = gate._keys("default")[0]
    assert gate.acquire("m1")[0] is True
    assert gate.acquire("m2")[0] is True
    assert redis_client.zcard(gkey) == 2
    granted, reason = gate.acquire("m3")
    assert granted is False and reason == "global_limit"
    gate.release("m1")
    assert gate.acquire("m4")[0] is True


def test_expired_lease_reclaimed(gate, redis_client):
    """kill 掉持槽进程 → 槽位残留但已过 TTL：后续 acquire 顺带回收。"""
    gkey = gate._keys("default")[0]
    now = time.time()
    # 两个已过期租约（score 早于 now-ttl），等价于崩溃进程持有的旧槽位
    redis_client.zadd(gkey, {"dead-1": now - 31, "dead-2": now - 60})
    # 若无回收逻辑，zcard=2 >= 2 应拒绝；回收后占位成功
    assert gate.acquire("live-1")[0] is True
    members = redis_client.zrange(gkey, 0, -1)
    assert "dead-1" not in members and "live-1" in members


def test_two_gates_share_global_limit(redis_client, monkeypatch):
    """两副本模拟：两个门实例（各自独立 request_id 空间）共享同一上限。"""
    redis_client.flushdb()
    monkeypatch.setattr(dg, "DIST_MAX_CONCURRENT_REQUESTS", 2)
    monkeypatch.setattr(dg, "DIST_TENANT_MAX_CONCURRENT", 0)
    gate_a, gate_b = DistributedGate(), DistributedGate()
    monkeypatch.setattr(gate_a, "_redis", lambda: redis_client)
    monkeypatch.setattr(gate_b, "_redis", lambda: redis_client)
    assert gate_a.acquire("a-1")[0] is True
    assert gate_b.acquire("b-1")[0] is True
    # 全局合计已到 2：任一副本的第三个请求都被拒
    assert gate_a.acquire("a-2")[0] is False
    assert gate_b.acquire("b-2")[0] is False
    gate_b.release("b-1")
    assert gate_a.acquire("a-3")[0] is True


def test_tenant_limit_stacks_on_global(redis_client, monkeypatch):
    redis_client.flushdb()
    monkeypatch.setattr(dg, "DIST_MAX_CONCURRENT_REQUESTS", 10)
    monkeypatch.setattr(dg, "DIST_TENANT_MAX_CONCURRENT", 1)
    gate = DistributedGate()
    monkeypatch.setattr(gate, "_redis", lambda: redis_client)
    assert gate.acquire("a-1", tenant="t1")[0] is True
    granted, reason = gate.acquire("a-2", tenant="t1")
    assert granted is False and reason == "tenant_limit"
    # 租户满不影响其他租户（全局未满）
    assert gate.acquire("b-1", tenant="t2")[0] is True
    # 租户槽位释放后恢复
    gate.release("a-1", tenant="t1")
    assert gate.acquire("a-3", tenant="t1")[0] is True


def test_release_idempotent(gate, redis_client):
    """release 幂等：重复调用与对不存在 member 调用均不报错。"""
    gate.acquire("m1")
    gate.release("m1")
    gate.release("m1")  # 重复 release
    gate.release("never-existed")
    assert redis_client.zcard(gate._keys("default")[0]) == 0
