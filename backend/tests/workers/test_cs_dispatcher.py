"""P6 dispatcher worker 契约测试。

worker 只做三件事：按租户队列顺序调用一次派单、写心跳、异常不退出循环。
真实数据库连接被替换为脚本化 session，避免把内存 fake 当成 PG 验收。
"""

from __future__ import annotations

import pytest

from backend.config import cs_dispatch as config
from backend.customer_service.dispatch import presence, service
from backend.customer_service.dispatch.outbox import RelayResult
from backend.customer_service.dispatch.reaper import ReapResult
from backend.workers import cs_dispatcher


class _FakeSession:
    def __init__(self) -> None:
        self.records: list[str] = []
        self.closed = False

    def record(self, name: str) -> None:
        self.records.append(name)

    def begin(self) -> "_Txn":
        return _Txn(self)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc) -> bool:
        self.closed = True
        return False


class _Txn:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        self._session.record("begin")
        return self._session

    async def __aexit__(self, exc_type, _exc, _tb) -> bool:
        self._session.record("rollback" if exc_type is not None else "commit")
        return False


class _FakeFactory:
    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        session = _FakeSession()
        self.sessions.append(session)
        return session


@pytest.fixture(autouse=True)
def enable_dispatch_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认把模式置为 enforce；模式门禁本身由专门用例覆盖。"""
    monkeypatch.setattr(cs_dispatcher.config, "CS_DISPATCH_MODE", "enforce")


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> _FakeFactory:
    fake = _FakeFactory()
    monkeypatch.setattr(cs_dispatcher, "AsyncSessionLocal", fake)
    return fake


@pytest.fixture
def heads(monkeypatch: pytest.MonkeyPatch) -> dict:
    state: dict = {"tenants": []}

    async def waiting_tenant_heads(*_a, **_k):
        return list(state["tenants"])

    monkeypatch.setattr(
        cs_dispatcher.repository, "waiting_tenant_heads", waiting_tenant_heads
    )
    return state


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录 dispatch_once 的租户调用顺序，默认全部 no_candidate。"""
    calls: list[str] = []
    outcomes: dict[str, str] = {}

    async def fake_dispatch(_session, *, tenant_id, now=None, dry_run=False):
        calls.append(tenant_id)
        return service.DispatchResult(status=outcomes.get(tenant_id, "no_candidate"))

    monkeypatch.setattr(cs_dispatcher.service, "dispatch_once", fake_dispatch)
    return calls


async def test_empty_queue_does_not_touch_dispatch(factory, heads, dispatched) -> None:
    result = await cs_dispatcher.run_once()

    assert result.status == "no_handoff"
    assert dispatched == []
    assert factory.sessions[0].records == ["begin", "commit"]


async def test_run_once_skips_tenants_without_candidates(
    factory, heads, dispatched, monkeypatch
) -> None:
    heads["tenants"] = ["tenant-a", "tenant-b"]

    async def fake_dispatch(_session, *, tenant_id, now=None, dry_run=False):
        dispatched.append(tenant_id)
        if tenant_id == "tenant-b":
            return service.DispatchResult(status="dispatched", agent_id="agent-b")
        return service.DispatchResult(status="no_candidate")

    monkeypatch.setattr(cs_dispatcher.service, "dispatch_once", fake_dispatch)

    result = await cs_dispatcher.run_once()

    assert dispatched == ["tenant-a", "tenant-b"]
    assert result.status == "dispatched"
    assert result.agent_id == "agent-b"


async def test_run_once_is_disabled_when_mode_is_off(
    factory, heads, dispatched, monkeypatch
) -> None:
    monkeypatch.setattr(cs_dispatcher.config, "CS_DISPATCH_MODE", "off")
    heads["tenants"] = ["tenant-a"]

    result = await cs_dispatcher.run_once()

    assert result.status == "disabled"
    assert dispatched == []
    assert factory.sessions == []


async def test_run_once_passes_dry_run_in_shadow_mode(
    factory, heads, dispatched, monkeypatch
) -> None:
    monkeypatch.setattr(cs_dispatcher.config, "CS_DISPATCH_MODE", "shadow")
    heads["tenants"] = ["tenant-a"]
    captured: list[bool] = []

    async def fake_dispatch(_session, *, tenant_id, now=None, dry_run=False):
        captured.append(dry_run)
        return service.DispatchResult(status="shadow", agent_id="agent-a")

    monkeypatch.setattr(cs_dispatcher.service, "dispatch_once", fake_dispatch)

    result = await cs_dispatcher.run_once()

    assert captured == [True]
    assert result.status == "shadow"


async def test_run_once_does_not_dry_run_in_enforce_mode(
    factory, heads, dispatched, monkeypatch
) -> None:
    heads["tenants"] = ["tenant-a"]
    captured: list[bool] = []

    async def fake_dispatch(_session, *, tenant_id, now=None, dry_run=False):
        captured.append(dry_run)
        return service.DispatchResult(status="no_candidate")

    monkeypatch.setattr(cs_dispatcher.service, "dispatch_once", fake_dispatch)

    await cs_dispatcher.run_once()

    assert captured == [False]


async def test_run_once_stops_after_the_first_binding(
    factory, heads, dispatched, monkeypatch
) -> None:
    heads["tenants"] = ["tenant-a", "tenant-b"]

    async def fake_dispatch(_session, *, tenant_id, now=None, dry_run=False):
        dispatched.append(tenant_id)
        return service.DispatchResult(status="dispatched", agent_id=f"agent-{tenant_id}")

    monkeypatch.setattr(cs_dispatcher.service, "dispatch_once", fake_dispatch)

    await cs_dispatcher.run_once()

    assert dispatched == ["tenant-a"]


async def test_run_once_reports_last_failure_when_nothing_dispatched(
    factory, heads, dispatched, monkeypatch
) -> None:
    heads["tenants"] = ["tenant-a"]
    dispatched.clear()

    async def fake_dispatch(_session, *, tenant_id, now=None, dry_run=False):
        dispatched.append(tenant_id)
        return service.DispatchResult(status="presence_unavailable")

    monkeypatch.setattr(cs_dispatcher.service, "dispatch_once", fake_dispatch)

    assert (await cs_dispatcher.run_once()).status == "presence_unavailable"


async def test_heartbeat_writes_instance_scoped_key(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_heartbeat(instance_id: str) -> bool:
        calls.append((instance_id, config.CS_DISPATCHER_HEARTBEAT_TTL_SECONDS))
        return True

    monkeypatch.setattr(presence, "write_dispatcher_heartbeat", fake_heartbeat)

    assert await cs_dispatcher.write_heartbeat("inst-7") is True
    assert calls == [("inst-7", 30)]


async def test_heartbeat_failure_does_not_raise(monkeypatch) -> None:
    async def exploding(_instance_id: str) -> bool:
        raise RuntimeError("redis down")

    monkeypatch.setattr(presence, "write_dispatcher_heartbeat", exploding)

    assert await cs_dispatcher.write_heartbeat("inst-7") is False


def test_instance_id_prefers_explicit_configuration(monkeypatch) -> None:
    monkeypatch.setenv("CS_DISPATCHER_INSTANCE_ID", "  dispatcher-2  ")

    assert cs_dispatcher.resolve_instance_id() == "dispatcher-2"


def test_instance_id_falls_back_to_hostname(monkeypatch) -> None:
    monkeypatch.delenv("CS_DISPATCHER_INSTANCE_ID", raising=False)
    monkeypatch.setattr(cs_dispatcher.socket, "gethostname", lambda: "host-abc")

    assert cs_dispatcher.resolve_instance_id() == "host-abc"


async def test_bind_hub_loop_does_not_start_an_extra_subscriber(monkeypatch) -> None:
    recorded: list[tuple[object, bool]] = []

    class _Hub:
        def bind_loop(self, loop, start_subscriber=True):
            recorded.append((loop, start_subscriber))

    monkeypatch.setattr(cs_dispatcher, "get_agent_hub", lambda: _Hub())

    cs_dispatcher.bind_hub_loop()

    assert len(recorded) == 1
    assert recorded[0][1] is False


async def test_run_forever_survives_iteration_errors(monkeypatch) -> None:
    outcomes = [RuntimeError("db down"), service.DispatchResult(status="no_handoff")]
    calls: list[int] = []
    sleeps: list[float] = []

    async def flaky():
        calls.append(len(calls))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return cs_dispatcher.TickResult(dispatch=outcome)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(cs_dispatcher, "run_tick", flaky)
    monkeypatch.setattr(cs_dispatcher, "_sleep", fake_sleep)
    monkeypatch.setattr(cs_dispatcher, "bind_hub_loop", lambda: None)

    results = await cs_dispatcher.run_forever(iterations=2)

    assert len(calls) == 2
    assert [r.status for r in results] == ["no_handoff"]
    assert sleeps == [config.CS_DISPATCH_INTERVAL_SECONDS]


async def test_run_forever_writes_a_heartbeat_each_iteration(monkeypatch) -> None:
    heartbeats: list[str] = []

    async def ok():
        return cs_dispatcher.TickResult(
            dispatch=service.DispatchResult(status="no_handoff")
        )

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cs_dispatcher, "run_tick", ok)
    monkeypatch.setattr(cs_dispatcher, "_sleep", fake_sleep)
    monkeypatch.setattr(cs_dispatcher, "bind_hub_loop", lambda: None)

    async def fake_heartbeat(instance_id: str) -> bool:
        heartbeats.append(instance_id)
        return True

    monkeypatch.setattr(cs_dispatcher, "write_heartbeat", fake_heartbeat)

    await cs_dispatcher.run_forever(iterations=3)

    assert len(heartbeats) == 3


def test_default_mode_is_off() -> None:
    assert config.CS_DISPATCH_MODE in {"off", "shadow", "enforce"}


def test_offer_timeout_matches_frozen_decision() -> None:
    assert config.CS_OFFER_TIMEOUT_SECONDS == 30
    assert config.CS_MAX_DISPATCH_ATTEMPTS == 5


async def test_tick_runs_reaper_and_relay_around_dispatch(monkeypatch) -> None:
    """tick 顺序固定为 reaper → dispatch → relay（回收后才能同 tick 重派）。"""
    order: list[str] = []

    async def fake_reap(now=None):
        order.append("reap")
        return ReapResult(released=1)

    async def fake_dispatch(now=None):
        order.append("dispatch")
        return service.DispatchResult(status="dispatched")

    async def fake_relay(now=None):
        order.append("relay")
        return RelayResult(scanned=1, published=1)

    monkeypatch.setattr(cs_dispatcher, "reap_stage", fake_reap)
    monkeypatch.setattr(cs_dispatcher, "run_once", fake_dispatch)
    monkeypatch.setattr(cs_dispatcher, "relay_stage", fake_relay)

    tick = await cs_dispatcher.run_tick()

    assert order == ["reap", "dispatch", "relay"]
    assert tick.status == "dispatched"
    assert tick.reaped == ReapResult(released=1)
    assert tick.relayed == RelayResult(scanned=1, published=1)


async def test_reap_stage_is_skipped_when_disabled(monkeypatch) -> None:
    async def explode(*_a, **_k):  # pragma: no cover - 不应被调用
        raise AssertionError("开关关闭时不得触库")

    monkeypatch.setattr(cs_dispatcher.config, "CS_REAPER_ENABLED", False)
    monkeypatch.setattr(cs_dispatcher.reaper, "reap_once", explode)

    assert await cs_dispatcher.reap_stage() is None


async def test_relay_stage_is_skipped_when_disabled(monkeypatch) -> None:
    async def explode(*_a, **_k):  # pragma: no cover - 不应被调用
        raise AssertionError("开关关闭时不得触库")

    monkeypatch.setattr(cs_dispatcher.config, "CS_OUTBOX_RELAY_ENABLED", False)
    monkeypatch.setattr(cs_dispatcher.outbox, "relay_pending_events", explode)

    assert await cs_dispatcher.relay_stage() is None


async def test_off_mode_still_reaps_and_relays(monkeypatch) -> None:
    """``off`` 只关派单：回收与投递是恢复/投递路径，必须继续运行。"""
    monkeypatch.setattr(cs_dispatcher.config, "CS_DISPATCH_MODE", "off")
    calls: list[str] = []

    async def fake_reap(now=None):
        calls.append("reap")
        return ReapResult()

    async def fake_relay(now=None):
        calls.append("relay")
        return RelayResult()

    monkeypatch.setattr(cs_dispatcher, "reap_stage", fake_reap)
    monkeypatch.setattr(cs_dispatcher, "relay_stage", fake_relay)

    tick = await cs_dispatcher.run_tick()

    assert tick.status == "disabled"
    assert calls == ["reap", "relay"]
