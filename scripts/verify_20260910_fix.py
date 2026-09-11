# -*- coding: utf-8 -*-
"""验证 2026-09-10 两项修复：
1. rule_router：RAG 关键词 2 个命中 → 强信号直接拍板（不再升级 LLM）
2. retrievers：Stage1 0 命中时 domain → kb 顺序放宽（空 KB 不再假拒答）
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── 1. RuleRouter 强信号 ──
from backend.orchestration.router.rule_router import RuleRouter

rr = RuleRouter()
d = rr.route("退款审核时间是多少？")
assert d is not None, "rule 应命中"
assert d.confidence >= 0.8, f"应强信号拍板, got {d.confidence}"
assert d.candidates[0].name == "rag.search"
print(f"[PASS] rule_router: 「退款审核时间是多少？」confidence={d.confidence} → {d.candidates[0].name}（规则层拍板，跳过 LLM）")

d2 = rr.route("退款金额是多少")
assert d2 is None or d2.confidence < 0.8, "单关键词应仍是弱信号"
print(f"[PASS] rule_router: 「退款金额是多少」仍为弱信号 (conf={getattr(d2,'confidence',None)})，保持 Vector/LLM 决策")

# ── 2. Stage1 顺序放宽 ──
from types import SimpleNamespace
from unittest.mock import MagicMock
from langchain_core.documents import Document
from backend.rag.context import RequestContext, set_context
from backend.rag.retrieval.retrievers import ChunkLevelRetriever

class FakeDocDB:
    """模拟 doc 库：biz_order KB 为空，rag_test_kb 有文档"""
    def similarity_search(self, query, k=15, filter=None):
        if filter and filter.get("kb_id") == "biz_order":
            return []          # 空 KB：无论 domain 是否放宽都 0 文档
        return [Document(page_content="x", metadata={"doc_id": "faq1", "kb_id": "rag_test_kb", "doc_keywords": "退款,审核"})]

    def get(self, where=None, **kw):
        if where and where.get("kb_id") == "biz_order":
            return {"ids": [], "documents": [], "metadatas": []}
        return {"ids": ["faq1"], "documents": ["x"], "metadatas": [{"doc_id": "faq1"}]}

fake_chunks = [Document(page_content="客服 1-2 个工作日内审核", metadata={
    "doc_id": "faq1", "chunk_id": "c1", "kb_id": "rag_test_kb", "similarity": 0.82})]

captured_filters = []

def fake_retrieve(query, k=5, doc_ids=None, metadata_filter=None, **kw):
    captured_filters.append(metadata_filter)
    return fake_chunks

retriever = ChunkLevelRetriever(
    doc_db=FakeDocDB(),
    vectordb=MagicMock(),
    chunk_retriever=SimpleNamespace(retrieve=fake_retrieve),
    bm25=SimpleNamespace(invoke=lambda q: []),
    k=5,
)

# 模拟 trace c8431b548b01 的请求上下文：query_analyzer 推 order 域 + kb 路由到空的 biz_order
set_context(RequestContext(metadata_filter={"kb_id": "biz_order", "business_domain": "order"}))

docs = retriever.invoke("退款审核时间是多少？")
assert docs, "顺序放宽后应能召回 rag_test_kb 的 chunk（旧实现此处返回空 → 假拒答）"
assert captured_filters and captured_filters[-1] in (None, {}), \
    f"最终检索 filter 应已放宽为空（全库），got {captured_filters[-1]}"
print(f"[PASS] retrievers: 空 KB(biz_order) 0 命中 → 顺序放宽后召回 {len(docs)} 个 chunk，"
      f"最终 filter={captured_filters[-1]}（全库检索）")
print(f"       内容: {docs[0].page_content[:40]}")

# 验证探测方法本身
assert retriever._filter_has_docs({"kb_id": "biz_order"}) is False
assert retriever._filter_has_docs({"kb_id": "rag_test_kb"}) is True
assert retriever._filter_has_docs({}) is True
print("[PASS] _filter_has_docs: 空 KB 探测准确")

print("\n=== 全部通过 ===")
