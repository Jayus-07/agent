"""test_tool_stats_aggregation.py — 工具统计多副本聚合（P1-4）。

覆盖：
  写侧（core/tool_runtime/metrics）：开关关零 Redis 访问；开启后 total/ok/
  七分类 field 与 TTL；异常软失败不抛
  读侧（admin_tools）：process 默认零变化；redis 源聚合；merged 两源相加；
  Redis 不可达软失败退化为可用数据
  集成（真 Redis db15，不可达 skip）：两「副本」各打点 → 读侧读到合计
"""
from __future__ import annotations

import pytest

import backend.core.tool_runtime.metrics as tr_metrics
from backend.core.tool_runtime.metrics import (
    _dispatch_tool_stats_redis,
    _write_tool_stats_redis,
)
from backend.core.tool_runtime.models import ToolResult, ToolStatus


def _result(status: ToolStatus = ToolStatus.SUCCESS, tool: str = "t1") -> ToolResult:
    return ToolResult(tool_name=tool, status=status, latency_ms=1)


# ── 写侧 ──────────────────────────────────────────


class _Recorder:
    """记录 pipeline 命令序列的假 Redis。"""

    def __init__(self):
        self.commands = []

    def pipeline(self):
        return self

    def hincrby(self, key, field, n):
        self.commands.append(("hincrby", key, field, n))

    def expire(self, key, ttl):
        self.commands.append(("expire", key, ttl))

    def execute(self):
        return []


def test_write_side_disabled_zero_redis(monkeypatch):
    """开关关：dispatch 直接返回，submit 不发生。"""
    monkeypatch.setattr(tr_metrics, "TOOL_STATS_REDIS_ENABLED", False)
    boom = RuntimeError("must not submit")
    monkeypatch.setattr(tr_metrics, "_STATS_POOL", type("P", (), {
        "submit": staticmethod(lambda *a: (_ for _ in ()).throw(boom))})())
    _dispatch_tool_stats_redis(_result())  # 不抛即通过


def test_write_side_fields_success(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: r)
    _write_tool_stats_redis(_result(ToolStatus.SUCCESS))
    kinds = [c[2] for c in r.commands if c[0] == "hincrby"]
    assert kinds == ["t1:total", "t1:ok"]
    expires = [c for c in r.commands if c[0] == "expire"]
    assert expires and expires[0][2] == 8 * 86400


def test_write_side_fields_error_class(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: r)
    _write_tool_stats_redis(_result(ToolStatus.TIMEOUT))
    kinds = [c[2] for c in r.commands if c[0] == "hincrby"]
    # TIMEOUT → 七分类 timeout
    assert kinds == ["t1:total", "t1:timeout"]


def test_write_side_redis_unavailable_silent(monkeypatch):
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: None)
    _write_tool_stats_redis(_result())  # 不抛即通过


def test_write_side_exception_swallowed(monkeypatch):
    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr("backend.infra.redis.client.get_redis", _boom)
    _write_tool_stats_redis(_result())  # 不抛即通过（旁路软失败）


# ── 读侧 ──────────────────────────────────────────


@pytest.fixture
def no_admin_auth(monkeypatch):
    from backend.app.api.routes import admin_tools as admin_tools_mod

    async def _allow(request):
        return None

    monkeypatch.setattr(admin_tools_mod, "require_admin_user", _allow)


def _fake_redis_with(data: dict):
    class _R:
        def hgetall(self, key):
            return data
    return _R()


def test_read_process_source_zero_change(monkeypatch, no_admin_auth):
    """默认 process 源：不触 Redis，行为与旧版一致。"""
    from backend.app.api.routes import admin_tools as mod

    monkeypatch.setattr(mod, "_collect_samples", lambda *a: {})
    monkeypatch.setattr(
        mod, "_collect_redis_tool_stats",
        lambda: (_ for _ in ()).throw(AssertionError("process 源不得读 Redis")))
    out = mod._aggregate_tool_stats("process")
    assert out["scope"] == "process"
    assert out["totals"]["calls"] == 0


def test_read_redis_source(monkeypatch, no_admin_auth):
    from backend.app.api.routes import admin_tools as mod

    monkeypatch.setattr(mod, "_collect_samples", lambda *a: {})
    monkeypatch.setattr("backend.infra.redis.client.get_redis",
                        lambda: _fake_redis_with({
                            "t1:total": "10", "t1:ok": "7", "t1:timeout": "3",
                        }))
    out = mod._aggregate_tool_stats("redis")
    assert out["scope"] == "redis"
    assert out["totals"]["calls"] == 10
    assert out["totals"]["success"] == 7
    t1 = next(t for t in out["tools"] if t["tool"] == "t1")
    assert t1["failures"] == 3
    assert t1["error_classes"] == {"timeout": 3.0}


def test_read_merged_sums_both_sources(monkeypatch, no_admin_auth):
    """merged：进程内 8+2 与 Redis 10 合计 → 20。"""
    from backend.app.api.routes import admin_tools as mod

    class _S:
        def __init__(self, name, labels, value):
            self.name, self.labels, self.value = name, labels, value

    class _M:
        def __init__(self, name, samples):
            self.name, self.samples = name, samples

    def fake_collect():
        yield _M("agent_tool_calls_total", [
            _S("agent_tool_calls_total",
               {"tool": "t1", "domain": "travel", "status": "success"}, 8),
            _S("agent_tool_calls_total",
               {"tool": "t1", "domain": "travel", "status": "failed"}, 2),
        ])

    monkeypatch.setattr("prometheus_client.REGISTRY.collect", fake_collect)
    monkeypatch.setattr("backend.infra.redis.client.get_redis",
                        lambda: _fake_redis_with({
                            "t1:total": "10", "t1:ok": "9", "t1:network_error": "1",
                        }))
    out = mod._aggregate_tool_stats("merged")
    assert out["scope"] == "merged"
    assert out["totals"]["calls"] == 20
    assert out["totals"]["success"] == 17
    t1 = next(t for t in out["tools"] if t["tool"] == "t1")
    assert t1["failures"] == 3
    assert t1["error_classes"] == {"network_error": 1.0}


def test_read_redis_unavailable_degrades(monkeypatch, no_admin_auth):
    """Redis 读失败软失败：merged 退化为进程内数据，不抛。"""
    from backend.app.api.routes import admin_tools as mod

    monkeypatch.setattr(mod, "_collect_samples", lambda *a: {})
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: None)
    out = mod._aggregate_tool_stats("merged")
    assert out["scope"] == "merged"
    assert out["totals"]["tools_seen"] == 0


# ── 集成（真 Redis db15）────────────────────────────


@pytest.fixture(scope="module")
def redis_client():
    try:
        import redis as redis_lib

        from backend.config.redis import REDIS_URL

        base = REDIS_URL.rsplit("/", 1)[0]
        if "@localhost:" in base:
            base = base.replace("@localhost:", "@127.0.0.1:")
        client = redis_lib.Redis.from_url(
            base + "/15", decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        client.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"本地 Redis 不可达，跳过统计聚合集成测试: {e}")
    yield client
    try:
        client.flushdb()
    except Exception:  # noqa: BLE001
        pass


def test_two_replica_writes_aggregate(redis_client, monkeypatch, no_admin_auth):
    """两副本模拟：写侧直调两次（各 1 total）→ 读侧读到合计 2。"""
    import backend.app.api.routes.admin_tools as mod
    import backend.core.tool_runtime.metrics as trm
    from datetime import date
    from backend.config.redis import REDIS_KEY_PREFIX

    redis_client.flushdb()
    key = f"{REDIS_KEY_PREFIX or 'agent:'}tool_stats:{date.today():%Y%m%d}"
    monkeypatch.setattr(trm, "TOOL_STATS_REDIS_ENABLED", True)
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: redis_client)

    # 副本 A：1 成功；副本 B：1 超时
    _write_tool_stats_redis(_result(ToolStatus.SUCCESS, tool="rt1"))
    _write_tool_stats_redis(_result(ToolStatus.TIMEOUT, tool="rt1"))

    monkeypatch.setattr(mod, "_collect_samples", lambda *a: {})
    out = mod._aggregate_tool_stats("redis")
    rt1 = next(t for t in out["tools"] if t["tool"] == "rt1")
    assert rt1["calls"] == 2
    assert rt1["success"] == 1
    assert rt1["error_classes"] == {"timeout": 1.0}
    assert redis_client.ttl(key) > 0  # TTL 已设置（8 天滚动分区）
