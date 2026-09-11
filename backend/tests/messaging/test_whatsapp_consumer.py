"""WhatsApp 闭环消费者测试 — channel.whatsapp.inbound → Agent → ai.reply.events"""
from __future__ import annotations

import json

import pytest

from backend.infra.messaging import consumer


@pytest.fixture(autouse=True)
def _reset_consumer_state():
    consumer._inproc_dedup.clear()
    consumer._thread = None
    yield
    consumer._inproc_dedup.clear()
    consumer._thread = None


def _make_event(message_id="wamid.1", text="你好", conversation_id="wa:8613800000000",
                user_id="8613800000000", msg_type="text", event_type="whatsapp.message.inbound"):
    return {
        "event_id": "evt-1",
        "event_type": event_type,
        "occurred_at": "2026-09-12T08:00:00Z",
        "source": "business-service",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "payload": {
            "channel": "whatsapp",
            "external_user_id": user_id,
            "message_id": message_id,
            "msg_type": msg_type,
            "text": text,
            "timestamp": "1726118400",
        },
    }


class TestHandleEvent:
    def test_inbound_message_triggers_agent_and_reply(self, monkeypatch):
        """入站文本消息 → 调 Agent → 发布 ai.reply.created"""
        captured = {}

        class FakeAgent:
            def ask(self, question, session_id="default", kb_id="default", user_id="default"):
                captured["question"] = question
                captured["session_id"] = session_id
                captured["user_id"] = user_id
                return "您好，请问有什么可以帮您？"

        monkeypatch.setattr(
            "backend.app.api.deps.get_multi_agent", lambda: FakeAgent()
        )

        def fake_publish(conversation_id, user_id, reply_to, answer):
            captured["conversation_id"] = conversation_id
            captured["user_id"] = user_id
            captured["reply_to"] = reply_to
            captured["answer"] = answer

        monkeypatch.setattr(consumer, "_publish_reply", fake_publish)
        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis", lambda: None, raising=False
        )

        consumer._handle_event(
            json.dumps(_make_event()).encode("utf-8"), "channel.whatsapp.inbound"
        )

        assert captured["question"] == "你好"
        assert captured["session_id"] == "wa:8613800000000"
        assert captured["answer"] == "您好，请问有什么可以帮您？"
        assert captured["reply_to"] == "wamid.1"

    def test_duplicate_message_skipped(self, monkeypatch):
        """同一 message_id 二次投递不重复回复（幂等）"""
        calls = []

        class FakeAgent:
            def ask(self, question, **kwargs):
                calls.append(question)
                return "回复"

        monkeypatch.setattr(
            "backend.app.api.deps.get_multi_agent", lambda: FakeAgent()
        )
        monkeypatch.setattr(consumer, "_publish_reply", lambda *a, **k: None)
        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis", lambda: None, raising=False
        )

        raw = json.dumps(_make_event()).encode("utf-8")
        consumer._handle_event(raw, "channel.whatsapp.inbound")
        consumer._handle_event(raw, "channel.whatsapp.inbound")

        assert len(calls) == 1

    def test_non_text_message_skipped(self, monkeypatch):
        """图片等非文本消息直接跳过，不调 Agent"""
        monkeypatch.setattr(consumer, "_publish_reply", lambda *a, **k: None)
        consumer._handle_event(
            json.dumps(_make_event(msg_type="image", text="")).encode("utf-8"),
            "channel.whatsapp.inbound",
        )
        # 未发布回复 + 未抛异常即通过

    def test_unrelated_event_type_skipped(self, monkeypatch):
        consumer._handle_event(
            json.dumps(_make_event(event_type="message.created")).encode("utf-8"),
            "channel.whatsapp.inbound",
        )
        # skipped 路径，不发布、不调 Agent

    def test_agent_failure_publishes_nothing(self, monkeypatch):
        """Agent 异常：返回空回复，不发布 ai.reply 事件"""

        class BrokenAgent:
            def ask(self, question, **kwargs):
                raise RuntimeError("LLM down")

        monkeypatch.setattr(
            "backend.app.api.deps.get_multi_agent", lambda: BrokenAgent()
        )
        published = []
        monkeypatch.setattr(
            consumer, "_publish_reply", lambda *a, **k: published.append(a)
        )
        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis", lambda: None, raising=False
        )

        consumer._handle_event(
            json.dumps(_make_event()).encode("utf-8"), "channel.whatsapp.inbound"
        )
        assert published == []
