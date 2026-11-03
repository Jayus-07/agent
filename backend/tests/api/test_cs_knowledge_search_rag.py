"""A7（cs-capability-gap）：坐席知识检索 RAG 扩展通道 T2 回归。

FAQ 速查层结果之上合并 RAG 文档级命中（source=rag）：
- 合并与契约：RAG 项 source=rag/matched=False/相似度语义分值/内容截断
- 降级：RAG 通道单库失败不 fail 接口，FAQ 结果照常返回
- 兼容：无 RAG 命中（打桩返回空）时响应与旧契约一致
RAG proxy 打桩（不依赖真实 rag-service）；FAQ 层走真库（p7trag- 前缀，
用后即清，同 test_cs_dashboard_knowledge.py 纪律）。
"""
from __future__ import annotations

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from backend.app.api.routes import cs_agent_offers, cs_knowledge

TENANT = "tenant-p7trag"
_PREFIX = "p7trag-"


def _pg():
    from backend.config.database import MEMORY_DB_CONFIG

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ai.cs_faq WHERE question LIKE %s",
                    (_PREFIX + "%",))
        conn.commit()


@pytest.fixture
def db():
    from backend.customer_service.faq import reset_faq_store_for_tests

    reset_faq_store_for_tests()
    _cleanup()
    yield
    _cleanup()
    reset_faq_store_for_tests()


@pytest.fixture
def agent_client():
    context = cs_agent_offers.AgentContext(
        agent_id=_PREFIX + "agent-a",
        tenant_id=TENANT,
        user_id=_PREFIX + "user-1",
        cs_role="agent",
        platform_roles=("viewer",),
    )
    app = FastAPI()
    app.include_router(cs_knowledge.router)
    app.dependency_overrides[cs_knowledge.require_agent] = lambda: context
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def rag_docs_factory():
    """可编程的 RAG proxy 打桩：按用例注入返回/异常。"""
    from backend.rag import client as rag_client

    holder: dict = {"docs": [], "error": None}

    class _StubProxy:
        def retrieve_documents(self, question, kb_id="default", top_k=5,
                               subject_type="", department="",
                               permissions=None, roles=(), tenant_id=""):
            if holder["error"] is not None:
                raise holder["error"]
            assert subject_type == "customer", "坐席 RAG 通道必须带客户主体授权"
            # 服务端按 kb 过滤：只返回属于该库的文档（真实契约）
            return [d for d in holder["docs"]
                    if (d.metadata or {}).get("kb_id") == kb_id]

    original = rag_client.get_rag_proxy
    rag_client.get_rag_proxy = lambda: _StubProxy()
    yield holder
    rag_client.get_rag_proxy = original


def _rag_doc(content: str, *, source_file: str = "refund-policy.md",
             score: float | None = 0.4) -> Document:
    meta = {"kb_id": "cs_policy", "doc_id": "deadbeef12",
            "source_file": source_file}
    if score is not None:
        meta["score"] = score
    return Document(page_content=content, metadata=meta)


def test_search_merges_faq_and_rag_channels(db, agent_client, rag_docs_factory):
    from backend.customer_service.faq import get_faq_store

    get_faq_store().upsert_faq(
        _PREFIX + "优惠券可以叠加吗", "一单仅可使用一张优惠券，不可叠加。",
        status="published",
    )
    rag_docs_factory["docs"] = [
        _rag_doc("## 2. 叠加规则 **默认不可叠加**：一单仅可使用一张优惠券。"),
    ]

    response = agent_client.get(
        "/cs/knowledge/search", params={"q": _PREFIX + "优惠券可以叠加吗"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]

    faq_items = [i for i in items if i["source"] == "faq"]
    rag_items = [i for i in items if i["source"] == "rag"]
    assert faq_items and faq_items[0]["matched"] is True
    assert rag_items, "RAG 命中必须并入 items"
    top_rag = rag_items[0]
    assert top_rag["matched"] is False
    assert top_rag["question"].startswith("【知识库】")
    assert "refund-policy.md" in top_rag["question"]
    assert "默认不可叠加" in top_rag["answer"]
    # 向量距离 0.4 → 相似度 0.6
    assert top_rag["score"] == pytest.approx(0.6, abs=1e-6)


def test_rag_channel_degrades_on_error_keeps_faq(db, agent_client,
                                                 rag_docs_factory):
    from backend.customer_service.faq import get_faq_store

    get_faq_store().upsert_faq(
        _PREFIX + "怎么开发票", "电子普票 1 个工作日内开具。", status="published",
    )
    rag_docs_factory["error"] = RuntimeError("rag-service 不可达")

    response = agent_client.get(
        "/cs/knowledge/search", params={"q": _PREFIX + "怎么开发票"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items and all(i["source"] == "faq" for i in items)
    assert any("开发票" in i["question"] for i in items)


def test_rag_only_results_when_faq_misses(db, agent_client, rag_docs_factory):
    """FAQ 无命中（min_score 过滤）时 RAG 项独立成结果。"""
    rag_docs_factory["docs"] = [
        _rag_doc("支付失败可先重试或更换支付方式。", source_file="payment-faq-policy.md",
                 score=None),  # BM25 补充结果无 score → 0.5 固定档
    ]

    response = agent_client.get(
        "/cs/knowledge/search", params={"q": _PREFIX + "完全无关联的查询词"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items and all(i["source"] == "rag" for i in items)
    assert items[0]["score"] == 0.5
    assert "payment-faq-policy.md" in items[0]["question"]


def test_rag_empty_content_documents_are_skipped(db, agent_client,
                                                 rag_docs_factory):
    rag_docs_factory["docs"] = [_rag_doc("   "), _rag_doc("有效内容")]

    response = agent_client.get(
        "/cs/knowledge/search", params={"q": _PREFIX + "任意查询"},
    )
    assert response.status_code == 200
    rag_items = [i for i in response.json()["items"] if i["source"] == "rag"]
    assert len(rag_items) == 1
    assert rag_items[0]["answer"] == "有效内容"
