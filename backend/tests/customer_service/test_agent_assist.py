"""test_agent_assist.py — 坐席辅助（批次A）单元测试。

覆盖：
  - 触发过滤：开关关闭 / 非命中事件 / 非用户消息 / 空 conversation_id
  - 会话级 in-flight 去重：同会话生成中重复触发只调度一次
  - 推荐生成：知识答案 / 拒答回退检索片段 / 订单速览 / top-k 截断
  - 任务收口：推送 assist.suggestion（persist=False）、失败静默、in-flight 清理
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import backend.customer_service.agent_assist as agent_assist
from backend.customer_service import agent_assist as aa
from backend.customer_service.knowledge.answer_decision import Decision

_CFG = "backend.config.customer_service"


@pytest.fixture(autouse=True)
def _reset_state():
    """每用例重置 in-flight 集合与信号量单例。"""
    with agent_assist._inflight_lock:
        agent_assist._inflight.clear()
    agent_assist._semaphore = None
    yield
    with agent_assist._inflight_lock:
        agent_assist._inflight.clear()
    agent_assist._semaphore = None


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(f"{_CFG}.CS_AGENT_ASSIST_ENABLED", True)
    monkeypatch.setattr(f"{_CFG}.CS_AGENT_ASSIST_TOP_K", 3)
    monkeypatch.setattr(f"{_CFG}.CS_AGENT_ASSIST_TIMEOUT_SECONDS", 2.0)


# ── 触发过滤 ─────────────────────────────────────────────


def test_skip_when_disabled(monkeypatch):
    monkeypatch.setattr(f"{_CFG}.CS_AGENT_ASSIST_ENABLED", False)
    aa.maybe_schedule_assist(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "user"}},
    )
    assert not agent_assist._inflight


def test_skip_irrelevant_event(enabled):
    aa.maybe_schedule_assist("heartbeat", {"conversation_id": "c1"})
    aa.maybe_schedule_assist("conversation.closed", {"conversation_id": "c1"})
    assert not agent_assist._inflight


def test_skip_non_user_message(enabled):
    aa.maybe_schedule_assist(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "human_agent"}},
    )
    aa.maybe_schedule_assist(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "assistant"}},
    )
    assert not agent_assist._inflight


def test_skip_empty_conversation_id(enabled):
    aa.maybe_schedule_assist(
        "message.created",
        {"conversation_id": "", "message": {"sender_type": "user"}},
    )
    assert not agent_assist._inflight


def test_schedule_marks_inflight(enabled):
    aa.maybe_schedule_assist(
        "message.created",
        {"conversation_id": "c1", "message": {"sender_type": "user"}},
    )
    assert "c1" in agent_assist._inflight


async def test_dedup_same_conversation(enabled):
    """同会话生成中再触发 → 不重复调度。"""
    release = asyncio.Event()

    async def _slow_ctx(cid):
        await release.wait()
        return None

    with patch.object(aa, "_load_conversation_context", _slow_ctx):
        aa.maybe_schedule_assist(
            "message.created",
            {"conversation_id": "c1", "message": {"sender_type": "user"}},
        )
        await asyncio.sleep(0)
        # 第二次触发被 in-flight 去重
        aa.maybe_schedule_assist(
            "message.created",
            {"conversation_id": "c1", "message": {"sender_type": "user"}},
        )
        tasks = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith("cs-assist-c1")
        ]
        assert len(tasks) == 1
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


# ── 推荐生成 ─────────────────────────────────────────────

_CONTEXT = {
    "conversation_id": "conv-1",
    "user_id": "u1",
    "tenant_id": "default",
    "trigger": "claimed",
    "messages": [
        {"sender_type": "user", "content": "我的订单什么时候到？"},
        {"sender_type": "assistant", "content": "正在为您查询"},
    ],
    "latest_intent": "logistics",
}


def _patch_knowledge(monkeypatch, decision, answer="预计 3 天内送达", conf=0.9):
    from backend.customer_service.knowledge.answer_decision import (
        CSAnswerDecision, Decision,
    )

    class _R:
        pass

    r = _R()
    r.decision = decision
    r.answer = answer
    r.confidence = conf
    r.suffix = ""

    class _SVC:
        def answer(self, question, kb_ids=None, session_id="default"):
            return r

    monkeypatch.setattr(
        "backend.customer_service.knowledge.service.get_knowledge_service",
        lambda: _SVC(),
    )
    return r


def test_generate_knowledge_answer(monkeypatch, enabled):
    _patch_knowledge(monkeypatch, Decision.ANSWER)
    recs = aa._generate_suggestions_sync(_CONTEXT)
    assert recs
    assert recs[0]["kind"] == "knowledge_answer"
    assert recs[0]["source"] == "知识库"
    assert recs[0]["score"] == pytest.approx(0.9)


def test_generate_cautious_annotated(monkeypatch, enabled):
    _patch_knowledge(monkeypatch, Decision.CAUTIOUS, conf=0.65)
    recs = aa._generate_suggestions_sync(_CONTEXT)
    assert recs[0]["kind"] == "knowledge_answer"
    assert "核实" in recs[0]["text"]


def test_generate_refuse_falls_back_to_snippet(monkeypatch, enabled):
    _patch_knowledge(monkeypatch, Decision.REFUSE, answer="")

    class _Doc:
        page_content = "物流说明：同城次日达，异地 3-5 个工作日送达。"
        metadata = {"source": "faq-01"}

    class _Retriever:
        def retrieve(self, q):
            return [_Doc()]

    class _Pipe:
        chunk_retriever = _Retriever()

        def _prepare_context(self, *a, **k):
            pass

        def _cleanup(self):
            pass

    with patch(
        "backend.rag.pipeline.get_rag_pipeline", lambda: _Pipe(),
    ):
        recs = aa._generate_suggestions_sync(_CONTEXT)
    kinds = [r["kind"] for r in recs]
    assert "knowledge_snippet" in kinds
    assert "knowledge_answer" not in kinds


def test_generate_order_summary(monkeypatch, enabled):
    _patch_knowledge(monkeypatch, Decision.REFUSE, answer="")

    class _Result:
        orders = [{
            "order_no": "DEMO-001", "status": "shipped",
            "total_amount": 199.0,
        }]
        total_count = 1

    class _SVC:
        def query_orders(self, user_id, order_id=None, query_type="list"):
            return _Result()

    monkeypatch.setattr(
        "backend.customer_service.service.order_service.get_order_service",
        lambda: _SVC(),
    )
    monkeypatch.setattr(
        "backend.customer_service.service.demo_mode.resolve_user_id",
        lambda uid: uid,
    )
    recs = aa._generate_suggestions_sync(_CONTEXT)
    kinds = [r["kind"] for r in recs]
    assert "order_summary" in kinds
    order_rec = next(r for r in recs if r["kind"] == "order_summary")
    assert "DEMO-001" in order_rec["text"]
    assert order_rec["source"] == "订单系统"


def test_generate_topk_truncation(monkeypatch, enabled):
    monkeypatch.setattr(f"{_CFG}.CS_AGENT_ASSIST_TOP_K", 1)
    _patch_knowledge(monkeypatch, Decision.ANSWER)
    recs = aa._generate_suggestions_sync(_CONTEXT)
    assert len(recs) <= 1


def test_generate_all_paths_fail(monkeypatch, enabled):
    """三路全失败 → 返回空，绝不抛异常。"""
    _patch_knowledge(monkeypatch, Decision.REFUSE, answer="")
    with patch(
        "backend.rag.pipeline.get_rag_pipeline",
        side_effect=RuntimeError("rag down"),
    ):
        recs = aa._generate_suggestions_sync(_CONTEXT)
    assert recs == []


def test_generate_empty_history(enabled):
    recs = aa._generate_suggestions_sync({**_CONTEXT, "messages": []})
    assert recs == []


# ── 任务收口 ─────────────────────────────────────────────


class _Hub:
    def __init__(self):
        self.published = []

    def publish(self, event_type, persist=True, **payload):
        self.published.append((event_type, persist, payload))


async def test_assist_task_publishes(enabled):
    async def _ctx(cid):
        return dict(_CONTEXT)

    def _gen(ctx):
        return [{
            "kind": "knowledge_answer", "source": "知识库",
            "score": 0.9, "text": "预计 3 天送达",
        }]

    hub = _Hub()
    with patch.object(aa, "_load_conversation_context", _ctx), \
         patch.object(aa, "_generate_suggestions_sync", _gen), \
         patch("backend.customer_service.realtime.get_agent_hub", lambda: hub):
        await aa._assist_task("conv-1")

    assert len(hub.published) == 1
    event_type, persist, payload = hub.published[0]
    assert event_type == "assist.suggestion"
    assert persist is False
    assert payload["conversation_id"] == "conv-1"
    assert payload["suggestions"][0]["text"] == "预计 3 天送达"
    # 任务收口后 in-flight 释放
    assert "conv-1" not in agent_assist._inflight


async def test_assist_task_silent_on_failure(enabled):
    async def _boom(cid):
        raise RuntimeError("db down")

    with patch.object(aa, "_load_conversation_context", _boom):
        await aa._assist_task("conv-err")  # 不应抛出
    assert "conv-err" not in agent_assist._inflight


async def test_assist_task_skips_ai_conversation(enabled):
    async def _ctx(cid):
        return None  # AI 阶段会话

    hub = _Hub()
    with patch.object(aa, "_load_conversation_context", _ctx), \
         patch("backend.customer_service.realtime.get_agent_hub", lambda: hub):
        await aa._assist_task("conv-ai")
    assert not hub.published
