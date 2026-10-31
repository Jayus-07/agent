"""cs-workstation-v2 P4：assist.suggestion 面板字段契约回归。

assist.suggestion 是 persist=False 的瞬态事件（不落 events 表），断言
走 stub hub 捕获 publish 调用。覆盖：
- 触发矩阵：user→message / human_agent→followup / bot·system→None /
  conversation.claimed→claimed / in-flight 去重
- payload 面板字段：intent（FINE_INTENTS 中文映射+置信 round）、
  emotion（强/弱负面规则）、sop（意图模板+默认模板）、trigger 透传
- 情绪 alert 时 suggestions 追加 emotion_alert 卡片
- 推荐为空但面板字段存在时仍发事件；全部为空才静默
- persist=False 契约不回归（瞬态、不占 seq）
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from backend.customer_service import agent_assist


@pytest.fixture(autouse=True)
def _fresh_state():
    agent_assist._inflight.clear()
    agent_assist._semaphore = None
    yield
    agent_assist._inflight.clear()
    agent_assist._semaphore = None


@pytest.fixture
def published(monkeypatch):
    """stub hub：捕获 publish 调用；assist 开关强制开。"""
    calls: list[dict] = []

    def _publish(event_type, *, persist=True, **payload):
        calls.append({"type": event_type, "persist": persist, **payload})

    monkeypatch.setattr(
        "backend.customer_service.realtime.get_agent_hub",
        lambda: SimpleNamespace(publish=_publish),
    )
    monkeypatch.setattr(agent_assist, "_assist_enabled", lambda: True)
    monkeypatch.setattr(
        agent_assist, "_get_semaphore", lambda: contextlib.nullcontext(),
    )
    # 绕过 async with：直接让上下文生成同步可见
    return calls


def _run_task(monkeypatch, context, trigger="message"):
    """同步驱动 _assist_task（信号量被 stub 为 None，DB 层被替换）。"""
    import asyncio

    async def _fake_load(conversation_id):
        return context

    async def _fake_generate(context2):
        return list(context.get("_suggestions") or [])

    monkeypatch.setattr(agent_assist, "_load_conversation_context", _fake_load)
    monkeypatch.setattr(
        agent_assist, "_generate_suggestions_sync",
        lambda ctx: list(ctx.get("_suggestions") or []),
    )
    asyncio.run(agent_assist._assist_task("conv-p4", trigger))


# ── 触发矩阵（纯逻辑）─────────────────────────────────────

def test_trigger_user_message():
    acquired = agent_assist._acquire_trigger(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "user"}},
    )
    assert acquired == ("c1", "message")


def test_trigger_agent_message_is_followup():
    acquired = agent_assist._acquire_trigger(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "human_agent"}},
    )
    assert acquired == ("c1", "followup")


def test_trigger_bot_message_ignored():
    assert agent_assist._acquire_trigger(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "assistant"}},
    ) is None


def test_trigger_claimed():
    acquired = agent_assist._acquire_trigger(
        "conversation.claimed", {"conversation_id": "c1"},
    )
    assert acquired == ("c1", "claimed")


def test_trigger_inflight_dedup():
    first = agent_assist._acquire_trigger(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "user"}},
    )
    second = agent_assist._acquire_trigger(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "user"}},
    )
    assert first == ("c1", "message")
    assert second is None


# ── 面板字段（纯逻辑）─────────────────────────────────────

def test_detect_emotion_rules():
    assert agent_assist._detect_emotion([]) is None
    # 无用户消息（仅坐席/AI 消息）无法判情绪 → None
    assert agent_assist._detect_emotion(
        [{"sender_type": "human_agent", "content": "气死"}],
    ) is None
    assert agent_assist._detect_emotion(
        [{"sender_type": "user", "content": "你们是骗子吧！"}],
    ) == {"label": "愤怒", "level": "alert"}
    assert agent_assist._detect_emotion(
        [{"sender_type": "user", "content": "发货太慢了，一直没到"}],
    ) == {"label": "不满", "level": "warn"}
    assert agent_assist._detect_emotion(
        [{"sender_type": "user", "content": "你好，查一下订单"}],
    ) == {"label": "平和", "level": "ok"}


def test_sop_for_known_and_default():
    sop = agent_assist._sop_for("c_complaint")
    assert sop[0] == {"label": "安抚情绪", "state": "done"}
    assert sop[1] == {"label": "登记工单", "state": "cur"}
    default = agent_assist._sop_for("k_unknown")
    assert default == agent_assist._sop_for(None)


def test_panel_intent_maps_chinese_and_rounds():
    context = {
        "latest_intent": "t_order_cancel",
        "latest_intent_confidence": 0.8732111,
    }
    intent = agent_assist._panel_intent(context)
    assert intent == {"name": "t_order_cancel", "label": "取消订单",
                      "confidence": 0.873}
    assert agent_assist._panel_intent({"latest_intent": None}) is None


# ── 端到端事件契约（stub hub）──────────────────────────────

def test_task_publishes_panel_fields(published, monkeypatch):
    context = {
        "conversation_id": "conv-p4",
        "trigger": "message",
        "messages": [{"sender_type": "user", "content": "你们就是个骗子平台"}],
        "latest_intent": "c_complaint",
        "latest_intent_confidence": 0.91,
        "_suggestions": [{
            "kind": "knowledge_answer", "source": "知识库",
            "score": 0.8, "text": "抱歉给您带来不便…",
        }],
    }
    _run_task(monkeypatch, context, trigger="message")

    assert len(published) == 1
    event = published[0]
    assert event["type"] == "assist.suggestion"
    assert event["persist"] is False  # 瞬态契约不回归
    assert event["trigger"] == "message"
    assert event["intent"]["label"] == "投诉"
    assert event["emotion"] == {"label": "愤怒", "level": "alert"}
    assert event["sop"][1]["label"] == "登记工单"
    # 情绪 alert 追加预警卡片，原推荐保留
    kinds = [s["kind"] for s in event["suggestions"]]
    assert kinds == ["knowledge_answer", "emotion_alert"]


def test_task_publishes_even_without_suggestions(published, monkeypatch):
    """推荐全失败但意图/情绪/SOP 可用时仍推面板字段。"""
    context = {
        "conversation_id": "conv-p4",
        "messages": [{"sender_type": "user", "content": "你好"}],
        "latest_intent": "k_faq",
        "latest_intent_confidence": None,
        "_suggestions": [],
    }
    _run_task(monkeypatch, context, trigger="followup")
    assert len(published) == 1
    event = published[0]
    assert event["trigger"] == "followup"
    assert event["suggestions"] == []
    assert event["emotion"] == {"label": "平和", "level": "ok"}
    assert event["sop"]  # 默认模板兜底


def test_task_silent_when_nothing_to_say(published, monkeypatch):
    """无意图、无推荐时静默（不推空事件）。"""
    context = {
        "conversation_id": "conv-p4",
        "messages": [],
        "latest_intent": None,
        "latest_intent_confidence": None,
        "_suggestions": [],
    }
    _run_task(monkeypatch, context, trigger="claimed")
    assert published == []
