# -*- coding: utf-8 -*-
"""关键词去词表化契约（P0-1/P0-2/P0-3，2026-10-19）。

治理结论：人工词表/领域词/Jieba 排名提取退役；检索召回由原文 BM25+
向量+RRF+Rerank 承担；Stage1 关键词门控移除；通用标识符识别仅做
精确匹配增强且防短型号误命中长型号。
"""
from langchain_core.documents import Document


# ── A. 通用标识符识别 ────────────────────────────────────────────────

def test_identifiers_basic_extraction():
    from backend.rag.preprocessing.keyword import extract_identifiers

    text = "请在 OA 系统提交（编号 FAQ-001），型号 AB-123 的设备，错误码 E-404。"
    ids = extract_identifiers(text)
    assert "FAQ-001" in ids
    assert any(v.replace("-", "") in {i.replace("-", "") for i in ids}
               for v in ("AB-123",)), f"型号应被识别: {ids}"
    assert "E-404" in ids
    # 纯中文词与普通单词不得混入
    assert "OA" not in ids and "系统" not in ids


def test_identifiers_no_short_prefix_false_positive():
    """短型号不得作为长型号的子串命中（AB123 ≠ AB1234-X）。"""
    from backend.rag.preprocessing.keyword import _IDENTIFIER_RE

    text = "AB1234-X 是长型号"
    hits = [m.group(1) for m in _IDENTIFIER_RE.finditer(text)]
    assert "AB123" not in hits, "零宽断言必须阻止短前缀误命中"
    assert hits == ["AB1234-X"], hits


def test_identifiers_casefold_dedupe_and_deterministic():
    from backend.rag.preprocessing.keyword import extract_identifiers

    text = "编号 FAQ-001 再次提到 faq-001 与 FAQ-001"
    ids1 = extract_identifiers(text)
    ids2 = extract_identifiers(text)
    assert ids1 == ids2, "输出必须确定可复现"
    assert sum(1 for i in ids1 if i.casefold() == "faq-001") == 1, "大小写归一去重"


def test_extract_rule_keywords_is_identifier_based():
    """extract_rule_keywords 复杂度特征消费点：不再返回业务词表词。"""
    from backend.rag.preprocessing.keyword import extract_rule_keywords

    out = extract_rule_keywords("普通中文句子，没有标识符，只有自然语言描述。")
    assert out == [], "无标识符文本不得产出普通词关键词"
    out2 = extract_rule_keywords("编号 SKU-99 与 GB-4944 标准。")
    assert "SKU-99" in out2 and "GB-4944" in out2


# ── B. doc/chunk 级关键词停发 ────────────────────────────────────────

def test_doc_level_keywords_retired():
    from backend.rag.preprocessing.keyword import (
        extract_doc_keywords, extract_doc_keywords_typed,
    )

    r = extract_doc_keywords("任意长文本，包含大量业务术语与品牌名。")
    assert r.rule_keywords == [] and r.llm_keywords == []
    r2 = extract_doc_keywords_typed("文本", allow_llm=False)
    assert r2.all_keywords() == []


def test_chunk_keywords_metadata_empty_on_index_path():
    """indexer 对 chunk 写空串（兼容字段），不再提取普通词。"""
    import ast as _ast
    import pathlib

    src = pathlib.Path("backend/rag/indexing/indexer.py").read_text(encoding="utf-8")
    assert '_chunk_kw(' not in src, "chunk 级规则关键词提取应已删除"
    assert 'extract_chunk_keywords_qwen_batch' not in src, "Qwen 残链应已删除"
    assert '"chunk_keywords"] = ""' in src


def test_no_llm_keyword_calls_remain():
    """无 LLM 关键词调用：全仓索引/检索路径不引用 LLM 关键词函数。"""
    import pathlib

    for f in ("backend/rag/indexing/indexer.py",
              "backend/rag/indexing/stages/metadata_stage.py",
              "backend/rag/retrieval/retrievers.py"):
        src = pathlib.Path(f).read_text(encoding="utf-8")
        for banned in ("extract_chunk_keywords_qwen", "extract_doc_keywords_llm",
                       "_extract_doc_keywords_proxy"):
            assert banned not in src, f"{f} 仍引用 {banned}"


# ── C. Stage1 门控移除（直通契约）────────────────────────────────────

def test_stage1_gate_passthrough_keeps_all_candidates():
    """零关键词交集的文档不再被排除：候选全保留、excluded 恒空。"""
    from backend.rag.retrieval.retrievers import ChunkLevelRetriever

    docs = [
        Document(page_content="x", metadata={"doc_id": "d1", "doc_keywords": ""}),
        Document(page_content="y", metadata={"doc_id": "d2", "doc_keywords": "无关词表词"}),
        Document(page_content="z", metadata={"doc_id": "d1"}),  # 重复 doc_id
    ]
    ids, info = ChunkLevelRetriever._filter_docs_by_keywords("自然语言问题", docs)
    assert ids == ["d1", "d2"], "候选全保留（去重）"
    assert info["excluded"] == []
    assert info["reason"] == "keyword_gate_removed_p0_2"


def test_stage1_no_keyword_extraction_called():
    """retrievers 不再从查询文本提取关键词。"""
    import pathlib

    src = pathlib.Path("backend/rag/retrieval/retrievers.py").read_text(encoding="utf-8")
    assert "extract_chunk_keywords" not in src
    assert "_score_by_keyword_overlap" not in src


# ── D. BM25 语料 = 原文（P0-4 确认契约）─────────────────────────────

def test_bm25_corpus_uses_page_content_not_keywords():
    from backend.rag.pipeline import RAGPipeline

    import inspect
    src = inspect.getsource(RAGPipeline._build_bm25_corpus_from_vectorstore)
    assert "page_content" in src
    assert "chunk_keywords" not in src and "doc_keywords" not in src
