"""STOP CS-A P0-5 回归：human_active 坐席离线自愈（reaper recovery）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.customer_service.handoff.dispatch import reaper


@pytest.fixture
def now():
    return datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def _cand(agent="agent-1", version=2, conv="c1", tenant="t1", hid="h1"):
    return {
        "tenant_id": tenant, "handoff_id": hid, "conversation_id": conv,
        "assigned_agent_id": agent, "assignment_version": version,
    }


class _FakeReaperEnv:
    """reaper 外设桩：repository / presence / lifecycle.transition_handoff。"""

    def __init__(self, monkeypatch, *, candidates, online, last_agent_msg_at,
                 locked_handoff=None):
        self.candidates = candidates
        self.online = online
        self.last_agent_msg_at = last_agent_msg_at
        self.locked_handoff = locked_handoff
        self.transitions = []
        self.timeout_seconds = 120

        import backend.customer_service.handoff.dispatch.repository as repo

        async def _list_candidates(session, *, now, limit):
            # 复刻真实现的时间预算语义
            from backend.config.cs_dispatch import (
                CS_HUMAN_ACTIVE_OFFLINE_TIMEOUT_SECONDS as T,
            )

            return [c for c in self.candidates]
        monkeypatch.setattr(repo, "list_human_active_candidates", _list_candidates)

        async def _lock_conversation(session, *, tenant_id, conversation_id):
            return object() if conversation_id in {
                c["conversation_id"] for c in self.candidates
            } else None
        monkeypatch.setattr(repo, "lock_conversation", _lock_conversation)

        async def _lock_stale(session, *, tenant_id, handoff_id,
                              assigned_agent_id, assignment_version):
            return self.locked_handoff or object()
        monkeypatch.setattr(repo, "lock_stale_human_active_handoff", _lock_stale)

        async def _last_msg(session, *, conversation_id):
            return self.last_agent_msg_at
        monkeypatch.setattr(repo, "last_human_agent_message_at", _last_msg)

        import backend.customer_service.handoff.dispatch.presence as presence

        async def _online(*, tenant_id, agent_ids):
            if self.online is None:
                return None
            return {a for a in agent_ids if a in self.online}
        monkeypatch.setattr(presence, "online_agent_ids", _online)

        import backend.customer_service.handoff.lifecycle as lifecycle

        recorded = self.transitions

        async def _transition(session, **kw):
            recorded.append(kw)
            return kw["handoff"]
        monkeypatch.setattr(lifecycle, "transition_handoff", _transition)


@pytest.mark.asyncio
async def test_offline_agent_recovered_to_waiting(monkeypatch, now):
    """坐席离线 + 超时无消息 → human_active 送回 waiting_human（version+1 由
    transition_handoff 消费，reaper 负责延展 deadline）。"""
    env = _FakeReaperEnv(
        monkeypatch,
        candidates=[_cand()],
        online=set(),                      # agent-1 离线
        last_agent_msg_at=now - timedelta(seconds=600),
    )
    class _Handoff:
        updated_at = now - timedelta(seconds=600)
        total_deadline_at = None
        assignment_version = 2
    env.locked_handoff = _Handoff()

    result = await reaper.reap_stale_human_active(session=object(), now=now, limit=50)
    assert result.recovered == 1
    (tx,) = env.transitions
    assert tx["target_state"] == "waiting_human"
    assert tx["clear_assigned_agent"] is True
    assert tx["bump_assignment_version"] is True
    assert tx["release_assignments"] == "released"
    assert tx["event_type"] == "conversation.human_active_recovered"


@pytest.mark.asyncio
async def test_online_agent_not_recovered(monkeypatch, now):
    """坐席仍在线 → 跳过。"""
    env = _FakeReaperEnv(
        monkeypatch, candidates=[_cand()], online={"agent-1"},
        last_agent_msg_at=now - timedelta(seconds=600),
    )
    result = await reaper.reap_stale_human_active(session=object(), now=now, limit=50)
    assert result.recovered == 0
    assert env.transitions == []


@pytest.mark.asyncio
async def test_presence_unavailable_fail_closed(monkeypatch, now):
    """Redis 不可用（None）→ fail-closed，整批跳过（绝不误恢复）。"""
    env = _FakeReaperEnv(
        monkeypatch, candidates=[_cand()], online=None,
        last_agent_msg_at=now - timedelta(seconds=600),
    )
    result = await reaper.reap_stale_human_active(session=object(), now=now, limit=50)
    assert result.recovered == 0
    assert result.contended == 1
    assert env.transitions == []


@pytest.mark.asyncio
async def test_grace_guard_recent_agent_message(monkeypatch, now):
    """锁内复核：坐席刚发过消息（grace 期内）→ 放弃本轮恢复。"""
    env = _FakeReaperEnv(
        monkeypatch, candidates=[_cand()], online=set(),
        last_agent_msg_at=now - timedelta(seconds=30),  # 30s < 120s 阈值
    )
    result = await reaper.reap_stale_human_active(session=object(), now=now, limit=50)
    assert result.recovered == 0
    assert env.transitions == []


@pytest.mark.asyncio
async def test_recovery_extends_total_deadline(monkeypatch, now):
    """自愈 = 主管重派同语义：总等待期重新计满（否则回到队列立即被关闭）。"""
    env = _FakeReaperEnv(
        monkeypatch, candidates=[_cand()], online=set(),
        last_agent_msg_at=now - timedelta(seconds=600),
    )
    class _Handoff:
        updated_at = now - timedelta(seconds=600)
        total_deadline_at = now - timedelta(seconds=1)  # 早已超期
        assignment_version = 2
    env.locked_handoff = _Handoff()

    await reaper.reap_stale_human_active(session=object(), now=now, limit=50)
    assert env.locked_handoff.total_deadline_at is not None
    assert env.locked_handoff.total_deadline_at > now


@pytest.mark.asyncio
async def test_reap_once_includes_recovery(monkeypatch, now):
    """reap_once 汇总 recovered 计数（dispatcher 观测口径）。"""
    monkeypatch.setattr(
        reaper, "reap_expired_offers", _fake_stage(scanned=0),
    )
    monkeypatch.setattr(
        reaper, "reap_overdue_waiting", _fake_stage(scanned=0),
    )
    async def _rec(session, *, now, limit):
        return reaper.ReapResult(scanned=1, recovered=1)
    monkeypatch.setattr(reaper, "reap_stale_human_active", _rec)

    result = await reaper.reap_once(session=object(), now=now, limit=10)
    assert result.recovered == 1


def _fake_stage(scanned):
    async def _f(session, *, now, limit):
        return reaper.ReapResult(scanned=scanned)
    return _f
