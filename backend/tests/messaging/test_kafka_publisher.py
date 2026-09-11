"""infra/messaging/kafka 单元测试 — producer 降级与事件发布契约"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def kafka_module(monkeypatch):
    """每次测试重置单例状态"""
    monkeypatch.setenv("KAFKA_ENABLED", "true")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9094")
    import backend.config.messaging as cfg
    import backend.infra.messaging.kafka as mod
    # config 在首次 import 时读取环境变量，测试内直接改模块属性
    monkeypatch.setattr(cfg, "KAFKA_ENABLED", True, raising=False)
    importlib.reload(mod)
    mod._producer = None
    mod._last_probe_fail = 0.0
    yield mod
    mod._producer = None
    mod._last_probe_fail = 0.0


class TestGetKafkaProducer:
    def test_disabled_returns_none(self, monkeypatch, kafka_module):
        monkeypatch.setenv("KAFKA_ENABLED", "false")
        import backend.config.messaging as cfg
        # publish_event 内部通过 config 读取，直接测 get_kafka_producer 分支
        with patch.object(cfg, "KAFKA_ENABLED", False):
            assert kafka_module.get_kafka_producer() is None

    def test_connection_failure_enters_cooldown(self, kafka_module):
        with patch("kafka.KafkaProducer", side_effect=Exception("no broker")):
            assert kafka_module.get_kafka_producer() is None
            # cooldown 生效：第二次不再尝试连接
            assert kafka_module.get_kafka_producer() is None

    def test_singleton(self, kafka_module):
        fake = MagicMock()
        with patch("kafka.KafkaProducer", return_value=fake):
            p1 = kafka_module.get_kafka_producer()
            p2 = kafka_module.get_kafka_producer()
            assert p1 is p2 is fake


class TestPublishEvent:
    def _fake_producer(self):
        producer = MagicMock()
        future = MagicMock()
        future.add_errback = MagicMock()
        producer.send.return_value = future
        return producer

    def test_publish_event_contract(self, kafka_module):
        producer = self._fake_producer()
        kafka_module._producer = producer

        ok = kafka_module.publish_event(
            "cs.conversation.events",
            "conversation.state_changed",
            "conv-1",
            "user-1",
            {"handling_mode": "human"},
        )
        assert ok is True
        args, kwargs = producer.send.call_args
        assert args[0] == "cs.conversation.events"
        assert kwargs["key"] == "conv-1"
        event = kwargs["value"]
        assert event["event_type"] == "conversation.state_changed"
        assert event["source"] == "ai-service"
        assert event["conversation_id"] == "conv-1"
        assert event["user_id"] == "user-1"
        assert event["payload"] == {"handling_mode": "human"}
        assert "event_id" in event and "occurred_at" in event

    def test_publish_event_no_producer_returns_false(self, kafka_module):
        kafka_module._producer = None
        kafka_module._last_probe_fail = 1e18  # 强制 cooldown，跳过真实连接
        ok = kafka_module.publish_event("cs.message.events", "message.created", "c", "u", {})
        assert ok is False

    def test_publish_event_send_exception_returns_false(self, kafka_module):
        producer = MagicMock()
        producer.send.side_effect = Exception("send failed")
        kafka_module._producer = producer
        ok = kafka_module.publish_event("cs.message.events", "message.created", "c", "u", {})
        assert ok is False
