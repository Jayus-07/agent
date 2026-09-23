"""CS Knowledge 身份贯通（2026-09-23 STOP E2 回归）。

修复前 knowledge expert → service → pipeline.ask_result 全程不带身份，
memory 落默认 "default"——所有 CS 会话共享同一长期记忆空间。锁定：

- expert 节点从 CSGraphState 平铺键读取 user_id/tenant_id 并透传；
- service.answer 传给 pipeline.ask_result（→ context.identity → memory）；
- 未认证会话显式命名空间 cs-anon:<session>，绝不共享 "default"。
"""
from unittest.mock import MagicMock

import pytest

from backend.customer_service.experts import knowledge as knowledge_expert
from backend.rag.pipeline import AskOutcome


class _Outcome(AskOutcome):
    pass


def _patch_pipeline(monkeypatch, captured):
    """桩 get_rag_pipeline：捕获 ask_result 调用参数，返回固定应答。"""
    stub = MagicMock()

    def _ask_result(*args, **kwargs):
        captured.append(kwargs)
        return AskOutcome(answer="知识库答案", sources=[],
                          answer_meta={"can_answer": True, "confidence": 0.9})

    stub.ask_result.side_effect = _ask_result
    monkeypatch.setattr("backend.rag.pipeline.get_rag_pipeline",
                        lambda: stub)
    return stub


def test_answer_propagates_authenticated_identity(monkeypatch):
    captured: list = []
    _patch_pipeline(monkeypatch, captured)
    from backend.customer_service.knowledge.service import CSKnowledgeService

    svc = CSKnowledgeService()
    svc.answer("退货政策是什么", session_id="cs-sess-1",
               user_id="agent-42", tenant_id="t-7")

    assert captured, "ask_result 未被调用"
    kwargs = captured[0]
    assert kwargs["user_id"] == "agent-42"
    assert kwargs["tenant_id"] == "t-7"
    assert kwargs["subject_type"] == "customer"


def test_anonymous_session_gets_explicit_namespace(monkeypatch):
    """无认证身份 → cs-anon:<session> 命名空间，绝不共享 "default"。"""
    captured: list = []
    _patch_pipeline(monkeypatch, captured)
    from backend.customer_service.knowledge.service import CSKnowledgeService

    svc = CSKnowledgeService()
    svc.answer("退货政策是什么", session_id="anon-sess-9")

    kwargs = captured[0]
    assert kwargs["user_id"] == "cs-anon:anon-sess-9"
    assert kwargs["user_id"] != "default"


def test_expert_node_reads_flat_state_identity(monkeypatch):
    """expert 节点从 CSGraphState 平铺键取身份并透传到 service。"""
    captured_service: list = []

    class _StubService:
        def answer(self, **kwargs):
            captured_service.append(kwargs)
            result = MagicMock()
            result.answer = "答案"
            result.decision.value = "answer"
            result.confidence = 0.9
            result.kb_ids = ["cs_faq"]
            result.source_documents = []
            return result

    monkeypatch.setattr(
        "backend.customer_service.knowledge.get_knowledge_service",
        lambda: _StubService())

    state = {
        "user_message": "怎么退款",
        "cs_route": {"intent": "k_faq", "kb_ids": ["cs_faq"]},
        "session_id": "cs-sess-2",
        "user_id": "agent-42",
        "tenant_id": "t-7",
    }
    knowledge_expert.knowledge_expert_node(state)

    assert captured_service, "service.answer 未被调用"
    kwargs = captured_service[0]
    assert kwargs["user_id"] == "agent-42"
    assert kwargs["tenant_id"] == "t-7"
    assert kwargs["session_id"] == "cs-sess-2"
