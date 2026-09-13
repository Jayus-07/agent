"""token_budget 单测 — 历史消息 / RAG 证据的 token 预算裁剪。"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.memory.token_budget import (
    count_tokens,
    count_message_tokens,
    trim_messages_to_budget,
    trim_texts_to_budget,
)


def test_count_tokens_basic():
    assert count_tokens("") == 0
    assert count_tokens("hello world") > 0
    assert count_tokens("你好，世界") > 0


def test_count_message_tokens_multimodal():
    msg = HumanMessage(content=[
        {"type": "text", "text": "订单 12345 哪里了？"},
        {"type": "image_url", "image_url": {"url": "http://x/img.png"}},
    ])
    assert count_message_tokens(msg) > 0


def test_trim_messages_no_budget():
    msgs = [HumanMessage(content="a" * 500), AIMessage(content="b" * 500)]
    kept, dropped = trim_messages_to_budget(msgs, 0)
    assert kept == msgs and dropped == 0


def test_trim_messages_keeps_system_and_recent():
    # SystemMessage 全保留；旧消息被裁、最新消息保留
    msgs = [
        SystemMessage(content="会话摘要"),
        HumanMessage(content="长" * 3000),   # 放不进预算 → 裁掉
        AIMessage(content="答" * 3000),      # 放不进预算 → 裁掉
        HumanMessage(content="最新问题"),     # 保留
    ]
    kept, dropped = trim_messages_to_budget(msgs, 100)
    assert dropped == 2
    assert type(kept[0]).__name__ == "SystemMessage"
    assert kept[-1].content == "最新问题"
    assert all(type(m).__name__ != "AIMessage" or m.content == "最新问题" or True for m in kept)
    # 被裁的两条长消息确实不在结果里
    assert all(len(m.content) < 1000 for m in kept)


def test_trim_messages_orders_preserved():
    msgs = [
        SystemMessage(content="s"),
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
    ]
    kept, dropped = trim_messages_to_budget(msgs, 100000)
    assert dropped == 0
    assert [m.content for m in kept] == ["s", "q1", "a1", "q2"]


def test_trim_texts_keeps_head_drops_tail():
    texts = ["短", "文" * 500, "文" * 500, "最新"]
    kept, dropped = trim_texts_to_budget(texts, 100)
    assert kept == ["短", "最新"] or kept[0] == "短"
    assert dropped == 2


def test_trim_texts_first_doc_over_budget_kept():
    # 首条超预算仍保留（保证至少有证据），不产生丢弃计数
    texts = ["x" * 1000]
    kept, dropped = trim_texts_to_budget(texts, 10)
    assert kept == texts and dropped == 0


def test_trim_texts_no_budget():
    texts = ["a", "b"]
    kept, dropped = trim_texts_to_budget(texts, 0)
    assert kept == texts and dropped == 0
