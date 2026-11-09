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
                user_id="8613800000000", msg_type="text", event_type="whatsapp.message.inbound",
                source="business-service", tenant_id="tenant_wa"):
    return {
        "event_id": "evt-1",
        "event_type": event_type,
        "occurred_at": "2026-09-12T08:00:00Z",
        "source": source,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
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
            def ask(self, question, session_id="default", kb_id="default",
                    user_id="default", tenant_id=""):
                captured["question"] = question
                captured["session_id"] = session_id
                captured["user_id"] = user_id
                captured["tenant_id"] = tenant_id
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
        assert captured["tenant_id"] == "tenant_wa"
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


class TestInboundStrictIdentity:
    """入站身份 fail-closed（安全审计 #39）：

    broker 消息体声明的身份不可直接信任：source 不在白名单、缺 tenant_id
    或缺 user_id 的事件必须被拒绝——不执行 ask、不发布回复。冒充对照：
    合法事件的 tenant_id 必须透传 ask，渠道主体不再与平台真实用户共享
    default 租户键空间。
    """

    def _reject_setup(self, monkeypatch):
        """stub Agent 与发布端，返回两个调用记录列表。"""
        agent_calls = []
        published = []

        class FakeAgent:
            def ask(self, question, **kwargs):
                agent_calls.append((question, kwargs))
                return "回复"

        monkeypatch.setattr(
            "backend.app.api.deps.get_multi_agent", lambda: FakeAgent()
        )
        monkeypatch.setattr(
            consumer, "_publish_reply", lambda *a, **k: published.append(a)
        )
        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis", lambda: None, raising=False
        )
        return agent_calls, published

    def test_missing_source_rejected(self, monkeypatch):
        """无凭据对照：source 缺失 → 拒绝，不调 Agent、不发布。"""
        agent_calls, published = self._reject_setup(monkeypatch)
        consumer._handle_event(
            json.dumps(_make_event(source="")).encode("utf-8"),
            "channel.whatsapp.inbound",
        )
        assert agent_calls == []
        assert published == []

    def test_untrusted_source_rejected(self, monkeypatch):
        """冒充对照：伪造 source（含自产 ai-service 回流）→ 拒绝。"""
        agent_calls, published = self._reject_setup(monkeypatch)
        consumer._handle_event(
            json.dumps(_make_event(source="ai-service")).encode("utf-8"),
            "channel.whatsapp.inbound",
        )
        assert agent_calls == []
        assert published == []

    def test_missing_tenant_rejected(self, monkeypatch):
        """租户维度缺失 → 拒绝（不再归一 default 与平台用户共享键空间）。"""
        agent_calls, published = self._reject_setup(monkeypatch)
        consumer._handle_event(
            json.dumps(_make_event(tenant_id="")).encode("utf-8"),
            "channel.whatsapp.inbound",
        )
        assert agent_calls == []
        assert published == []

    def test_missing_user_id_rejected(self, monkeypatch):
        """无凭据对照：user_id 缺失 → 拒绝（废除 'whatsapp' 共享主体回退）。"""
        agent_calls, published = self._reject_setup(monkeypatch)
        event = _make_event(user_id="")
        event["payload"]["external_user_id"] = ""
        consumer._handle_event(
            json.dumps(event).encode("utf-8"), "channel.whatsapp.inbound"
        )
        assert agent_calls == []
        assert published == []

    def test_tenant_passthrough_to_ask(self, monkeypatch):
        """合法事件：tenant_id 透传 ask——渠道主体按声明租户隔离。"""
        agent_calls, published = self._reject_setup(monkeypatch)
        consumer._handle_event(
            json.dumps(_make_event()).encode("utf-8"),
            "channel.whatsapp.inbound",
        )
        assert len(agent_calls) == 1
        _, kwargs = agent_calls[0]
        assert kwargs.get("tenant_id") == "tenant_wa"
        assert kwargs.get("user_id") == "8613800000000"
        assert len(published) == 1
