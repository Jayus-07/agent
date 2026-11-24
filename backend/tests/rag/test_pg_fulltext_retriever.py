"""test_pg_fulltext_retriever.py
PGFullTextRetriever 单元测试：
1. 中文分词与 tsquery 生成
2. 符号清洗与英文/编号保留
3. Retriever 契约与 invoke() 返回格式
4. 异常隔离与安全性
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

from backend.rag.retrieval.pg_fulltext_retriever import (
    PGFullTextRetriever,
    build_simple_tsquery,
    _tokenize_for_pg_tsquery,
)


def test_tokenize_for_pg_tsquery_chinese_and_symbols():
    text = "退换货 & 工单(规则) : TEST-CS-001"
    tokens = _tokenize_for_pg_tsquery(text)
    assert len(tokens) > 0
    # 标点符号应被清洗，保留核心词
    assert any("退换" in t for t in tokens)
    assert any("规则" in t for t in tokens)
    assert any("TEST" in t or "CS" in t or "001" in t for t in tokens)
    # 纯特殊标点符号不应独立作为 term
    assert "&" not in tokens
    assert ":" not in tokens
    assert "(" not in tokens


def test_build_simple_tsquery():
    text = "客服规则"
    tsquery = build_simple_tsquery(text)
    assert "|" in tsquery
    assert "'客服'" in tsquery
    assert "'规则'" in tsquery

    # 空文本测试
    assert build_simple_tsquery("") == ""
    assert build_simple_tsquery("   ") == ""
    assert build_simple_tsquery("&&& :::") == ""


def test_pg_fulltext_retriever_contract():
    retriever = PGFullTextRetriever(collection="chroma", k=10, kb_id="rag_eval_kb")
    assert retriever.collection == "chroma"
    assert retriever.k == 10
    assert retriever.kb_id == "rag_eval_kb"


def test_pg_fulltext_retriever_mock_invoke():
    retriever = PGFullTextRetriever(collection="chroma", k=5, kb_id="rag_eval_kb")

    fake_row = {
        "id": "chroma:doc1:0",
        "doc_id": "doc1",
        "content": "客服退货退款操作细则",
        "metadata": {"department": "cs", "kb_id": "rag_eval_kb"},
        "rank_score": 0.854,
    }

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [fake_row]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        docs = retriever.invoke("客服退换货")

    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, Document)
    assert doc.page_content == "客服退货退款操作细则"
    assert doc.metadata["doc_id"] == "doc1"
    assert doc.metadata["bm25_score"] == pytest.approx(0.854)
    assert doc.metadata["vector_id"] == "chroma:doc1:0"


def test_pg_fulltext_retriever_db_exception_graceful():
    retriever = PGFullTextRetriever(collection="chroma", k=5)

    with patch("psycopg2.connect", side_effect=Exception("DB connection refused")):
        docs = retriever.invoke("任意问题")

    # 遇到底层连接故障应安全返回空列表，不抛异常打断上层流程
    assert docs == []


def test_fulltext_engine_config_default():
    from backend.config.rag import FULLTEXT_ENGINE
    assert FULLTEXT_ENGINE in ("pg_gin", "bm25")

