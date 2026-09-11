"""cutover 阶段 C 写权切换测试 — CS_WRITE_SOURCE=java 时状态/消息写入代理到 Java"""
from __future__ import annotations

import pytest

from backend.customer_service import conversation_store, state_transition


class TestStateTransitionJavaWrite:
    def setup_method(self):
        # 单例可能被其他测试初始化过，重置以隔离 CS_WRITE_SOURCE 影响
        state_transition._service_instance = None

    def test_default_is_local(self, monkeypatch):
        import backend.config.messaging as cfg
        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "local", raising=False)
        assert state_transition._use_java_write() is False

    def test_java_source_flag(self, monkeypatch):
        import backend.config.messaging as cfg
        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)
        assert state_transition._use_java_write() is True

    def test_java_apply_proxies_to_java(self, monkeypatch):
        import backend.config.messaging as cfg
        from backend.infra.http import business_client

        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)

        captured = {}

        def fake_post(path, body):
            captured["path"] = path
            captured["body"] = body
            return {
                "success": True,
                "confirmation_state": "confirmed",
                "handoff_state": "ai_active",
                "conversation_status": "open",
                "handling_mode": "ai",
                "pending_action": None,
                "errors": [],
            }

        monkeypatch.setattr(business_client, "post_json_sync", fake_post)
        svc = state_transition.get_state_transition_service()
        result = svc.apply({
            "user_id": "u1", "session_id": "s1", "conversation_id": "c1",
            "confirmation_target": "confirmed",
        })

        assert result["success"] is True
        assert result["confirmation_state"] == "confirmed"
        assert captured["path"] == "/internal/state-transitions"
        assert captured["body"]["confirmation_target"] == "confirmed"

    def test_java_apply_unavailable_returns_failure(self, monkeypatch):
        """java 写权模式下 Java 不可用：返回失败，不静默回落本地写"""
        import backend.config.messaging as cfg
        from backend.infra.http import business_client

        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)

        def failing_post(path, body):
            raise business_client.BusinessServiceError("connection refused")

        monkeypatch.setattr(business_client, "post_json_sync", failing_post)
        svc = state_transition.get_state_transition_service()
        result = svc.apply({"user_id": "u1", "session_id": "s1"})

        assert result["success"] is False
        assert any("business-service unavailable" in e for e in result["errors"])

    def test_java_load_snapshot_proxies(self, monkeypatch):
        import backend.config.messaging as cfg
        from backend.infra.http import business_client

        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)

        captured = {}

        def fake_get(path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"conversation_status": "open", "handling_mode": "manual",
                    "handoff_state": "agent_active", "confirmation_state": "not_required",
                    "pending_action": None}

        monkeypatch.setattr(business_client, "get_json_sync", fake_get)
        svc = state_transition.get_state_transition_service()
        snap = svc.load_snapshot("u1", "s1", "c1")

        assert snap["handling_mode"] == "manual"
        assert captured["path"] == "/internal/state-snapshot"
        assert captured["params"]["conversation_id"] == "c1"

    def test_java_load_snapshot_unavailable_returns_defaults(self, monkeypatch):
        import backend.config.messaging as cfg
        from backend.infra.http import business_client

        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)

        def failing_get(path, params=None):
            raise business_client.BusinessServiceError("timeout")

        monkeypatch.setattr(business_client, "get_json_sync", failing_get)
        svc = state_transition.get_state_transition_service()
        snap = svc.load_snapshot("u1", "s1", "c1")
        assert snap == state_transition._default_snapshot()


class TestConversationStoreJavaWrite:
    def test_java_record_posts_two_messages(self, monkeypatch):
        import backend.config.messaging as cfg
        from backend.infra.http import business_client

        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)

        captured = []

        def fake_post(path, body):
            captured.append((path, body))
            return {"message_id": "m-1", "created_at": "2026-09-12T00:00:00Z"}

        monkeypatch.setattr(business_client, "post_json_sync", fake_post)
        # Kafka 未启用时 publish_event 内部自降级，不 mock 也不会失败
        conversation_store.record_cs_turn(
            "c1", "u1", "问题", "回答",
            trace_id="t-1", cs_route={"intent": "order_query", "confidence": 0.9, "domain": "order"},
        )

        assert len(captured) == 2
        user_msg = captured[0][1]
        assistant_msg = captured[1][1]
        assert user_msg["sender_type"] == "user"
        assert user_msg["content"] == "问题"
        assert user_msg["trace_id"] == "t-1"
        assert assistant_msg["sender_type"] == "assistant"
        assert assistant_msg["content"] == "回答"
        assert assistant_msg["metadata"]["intent_name"] == "order_query"

    def test_java_record_unavailable_does_not_raise(self, monkeypatch):
        """java 写权模式下 Java 不可用：记录 warning，不抛异常（fire-and-forget 语义保留）"""
        import backend.config.messaging as cfg
        from backend.infra.http import business_client

        monkeypatch.setattr(cfg, "CS_WRITE_SOURCE", "java", raising=False)

        def failing_post(path, body):
            raise business_client.BusinessServiceError("connection refused")

        monkeypatch.setattr(business_client, "post_json_sync", failing_post)
        # 不抛异常即通过（调用方是图节点，异常会中断对话链路）
        conversation_store.record_cs_turn("c1", "u1", "问题", "回答")
