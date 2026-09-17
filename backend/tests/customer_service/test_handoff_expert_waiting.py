"""HandoffExpert 自动进入排队（HANDOFF_REQUESTED → WAITING_HUMAN）测试。

2026-09-17 修复：此前生产代码无任何位置执行这一步，工单永久卡在
handoff_requested，坐席认领 409、工作台输入框永远锁定。
"""
from __future__ import annotations

from backend.customer_service.experts.handoff import execute_handoff
from backend.customer_service.handoff import HandoffState


class _FakeStore:
    """内存版 HandoffStore 桩（只暴露 execute_handoff 用到的接口）。"""

    def __init__(self):
        self.saved: list[dict] = []

    def load(self, user_id, session_id):
        return self.saved[-1] if self.saved else None

    def save(self, user_id, session_id, handoff_data):
        self.saved.append(dict(handoff_data))


def test_execute_handoff_enters_waiting_human(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: store,
    )

    result = execute_handoff("我要转人工", {
        "user_id": "u-test",
        "session_id": "conv-test",
        "conversation_id": "conv-test",
    })

    # 专家返回态即排队中（cs_context 由此取值，拦截语义不变）
    assert result["data"]["handoff_state"] == (
        HandoffState.WAITING_HUMAN.value
    )

    # store 两次落盘：先 HANDOFF_REQUESTED，再 WAITING_HUMAN
    states = [s["handoff_state"] for s in store.saved]
    assert states == [
        HandoffState.HANDOFF_REQUESTED.value,
        HandoffState.WAITING_HUMAN.value,
    ]
    assert result["data"]["handling_mode"] == "human"
    assert result["response_draft"], "转接提示语不能为空"
