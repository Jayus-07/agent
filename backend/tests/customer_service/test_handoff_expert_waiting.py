"""HandoffExpert 自动进入排队（HANDOFF_REQUESTED → WAITING_HUMAN）测试。

2026-09-17 修复：此前生产代码无任何位置执行这一步，工单永久卡在
handoff_requested，坐席认领 409、工作台输入框永远锁定。
"""
from __future__ import annotations

from backend.customer_service.experts.handoff import execute_handoff
from backend.customer_service.handoff import HandoffState


class _FakeStore:
    """内存版 HandoffStore 桩（只暴露 execute_handoff 用到的接口）。

    STOP CS-A P0-6：写路径已收敛到 lifecycle.enter_waiting_handoff_sync，
    复用判定改走 get_active_by_conversation（DB-first）——桩同步暴露这两个
    接口；「落盘」由 lifecycle 桩记录。
    """

    def __init__(self):
        self.saved: list[dict] = []

    def load(self, user_id, session_id):
        return self.saved[-1] if self.saved else None

    def get_active_by_conversation(self, conversation_id):
        return self.saved[-1] if self.saved else None


def test_execute_handoff_enters_waiting_human(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: store,
    )
    persisted: list[dict] = []

    def _fake_enter(**kwargs):
        persisted.append(dict(kwargs))
        return {
            "handoff_id": "hex-1",
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "updated_at": "2026-10-07T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "backend.customer_service.handoff.lifecycle.enter_waiting_handoff_sync",
        _fake_enter,
    )

    result = execute_handoff("我要转人工", {
        "user_id": "u-test",
        "session_id": "conv-test",
        "conversation_id": "conv-test",
        "tenant_id": "default",
    })

    # 专家返回态即排队中（cs_context 由此取值，拦截语义不变）
    assert result["data"]["handoff_state"] == (
        HandoffState.WAITING_HUMAN.value
    )

    # P0-6：单次持久化、最终态 WAITING_HUMAN（lifecycle 唯一入口，
    # 同事务维护 conversations.handling_mode，不再经 store 两跳写）
    assert len(persisted) == 1
    assert persisted[0]["trigger_type"] == "explicit_request"

    states = [s["handoff_state"] for s in store.saved]
    assert states == []
    assert result["data"]["handling_mode"] == "human"
    assert result["response_draft"], "转接提示语不能为空"
