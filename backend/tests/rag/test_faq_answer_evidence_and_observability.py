# -*- coding: utf-8 -*-
"""FAQ 答案证据资格 / 摘要句边界 / 关键词归一 / parent 召回过滤 回归。

来源：ragu1 隔离栈 faq_only.docx 真实入库实证（2026-10-19）——
虚拟 parent（问题清单）以独立证据进入检索 Top-5、抽取式摘要按空格切句
产生残缺摘要、关键词 FAQ/faq 大小写重复、page_count 由 chunk 数冒充。
"""
from __future__ import annotations

from langchain_core.documents import Document


# ── A. 摘要句边界（_extract_first_sentences） ─────────────────────────

def test_summary_qa_pair_keeps_question_and_answer_intact():
    from backend.rag.preprocessing.llm_enrichment import _extract_first_sentences

    text = "Q：年假怎么申请?\nA：请在 OA 系统提交年假申请,经直属上级审批、HR 备案后生效。(编号 FAQ-001)\nQ：下一问?\nA：下一答。"
    s = _extract_first_sentences(text, 2)
    # 问题完整（不被空格/半角点切碎）、答案以原文为单元
    assert "年假怎么申请" in s
    assert "OA 系统提交年假申请" in s, "答案句内的空格不得被当作句界切碎"
    assert "下一问" not in s, "第二个 QA 对不得混入摘要"


def test_summary_plain_text_respects_sentence_boundaries():
    from backend.rag.preprocessing.llm_enrichment import _extract_first_sentences

    text = "本制度适用于全体员工。 请在 OA 系统提交申请。 后续条款略。"
    s = _extract_first_sentences(text, 2)
    assert s.startswith("本制度适用于全体员工。")
    assert "请在 OA 系统提交申请。" in s, "句内空格不再切句"


# ── B. 关键词大小写归一 ──────────────────────────────────────────────

def test_dedupe_keywords_normalized_merges_case_variants():
    from backend.rag.preprocessing.keyword import dedupe_keywords_normalized

    out = dedupe_keywords_normalized(
        ["faq", "编号", "OA", "FAQ", "直属", "HR", "申请", "备案", "年假", "生效", "faq"])
    lowered = [w.casefold() for w in out]
    assert len(lowered) == len(set(lowered)), "大小写变体必须合并"
    assert lowered.count("faq") == 1, "faq/FAQ 合并为一条"
    assert out[0] == "faq", "保序：保留首个出现形式"


def test_dedupe_keywords_empty_and_limit():
    from backend.rag.preprocessing.keyword import dedupe_keywords_normalized

    assert dedupe_keywords_normalized([]) == []
    assert dedupe_keywords_normalized(["A", "a", "B"], limit=1) == ["A"]


# ── C. 答案证据资格审计（质量门禁） ─────────────────────────────────

def _mk(chunk_id, granularity, content):
    return Document(page_content=content, metadata={"chunk_id": chunk_id,
                                                    "granularity": granularity})


def test_audit_answer_evidence_flags_question_only_parent():
    from backend.rag.preprocessing.quality_gate import audit_answer_evidence

    parent = _mk("p1", "parent", "年假怎么申请?\n年假需要提前多久申请?\n病假怎么申请?")
    leaf = _mk("l1", "leaf", "Q：年假怎么申请?\nA：请在 OA 提交。(编号 FAQ-001)")
    res = audit_answer_evidence([parent, leaf])
    assert res["count"] == 1
    assert res["suspect_no_answer_chunks"] == ["p1"]


def test_audit_answer_evidence_ignores_leaf_and_answer_parent():
    from backend.rag.preprocessing.quality_gate import audit_answer_evidence

    leaf = _mk("l1", "leaf", "Q：年假怎么申请?\nA：请在 OA 提交。")
    # parent 含答案内容 → 不误报
    parent_with_answer = _mk("p2", "parent", "第一章 总则?\n答：本章规定适用范围。")
    res = audit_answer_evidence([leaf, parent_with_answer])
    assert res["count"] == 0


def test_audit_answer_evidence_single_line_not_flagged():
    from backend.rag.preprocessing.quality_gate import audit_answer_evidence

    single = _mk("p3", "parent", "仅一行标题?")
    assert audit_answer_evidence([single])["count"] == 0


# ── D. parent 独立召回过滤（CustomRetriever 出口） ───────────────────

class _FakeVectorDB:
    """按预置顺序返回 (doc, score)；记录 k 请求用于 over-fetch 断言。"""

    def __init__(self, docs_with_scores):
        self.docs_with_scores = docs_with_scores
        self.requested_k = None

    def similarity_search_with_score(self, query, k=5, filter=None):
        self.requested_k = k
        return self.docs_with_scores[:k]


def test_custom_retriever_filters_independent_parent_hits(monkeypatch):
    from backend.rag.base import CustomRetriever
    from backend.config import rag as rag_cfg

    monkeypatch.setattr(rag_cfg, "RAG_FILTER_PARENT_RECALL", True)
    parent = Document(page_content="年假怎么申请?\n年假需要提前多久申请?",
                      metadata={"chunk_id": "p1", "granularity": "parent",
                                "doc_id": "d1"})
    leaf = Document(page_content="Q：年假怎么申请?\nA：请在 OA 提交。",
                    metadata={"chunk_id": "l1", "granularity": "leaf",
                              "doc_id": "d1"})
    db = _FakeVectorDB([(parent, 0.98), (leaf, 0.95)])
    r = CustomRetriever(db)
    out = r.retrieve("年假怎么申请", k=2)
    assert db.requested_k == 4, "过滤开启时必须 over-fetch k*2"
    assert [d.metadata["chunk_id"] for d in out] == ["l1"], "parent 独立命中被过滤"


def test_custom_retriever_keeps_parent_when_filter_disabled(monkeypatch):
    from backend.rag.base import CustomRetriever
    from backend.config import rag as rag_cfg

    monkeypatch.setattr(rag_cfg, "RAG_FILTER_PARENT_RECALL", False)
    parent = Document(page_content="问题列表", metadata={"chunk_id": "p1",
                                                        "granularity": "parent",
                                                        # ReviewFilter 对缺 doc_id
                                                        # 结果 fail-closed（对客不可见）
                                                        "doc_id": "d1"})
    db = _FakeVectorDB([(parent, 0.98)])
    out = CustomRetriever(db).retrieve("q", k=2)
    assert [d.metadata["chunk_id"] for d in out] == ["p1"]


# ── E. parse span 页数语义（indexer 侧纯逻辑抽验） ───────────────────

def test_parse_span_page_count_not_chunk_count():
    """非 PDF 的 page_count 必须为 0 且标记不适用（indexer.py P1 修复契约）。"""
    import ast as _ast
    import pathlib

    src = pathlib.Path("backend/rag/indexing/indexer.py").read_text(encoding="utf-8")
    assert '"page_count": len(chunks)' not in src, "禁止把 chunk 数写成页数"
    assert '"page_count_applicable"' in src, "必须显式标记页数适用性"
