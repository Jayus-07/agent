# -*- coding: utf-8 -*-
"""f6 回归测试：Chroma where 多顶层键必须显式 $and 包裹。

缺陷背景：_prepare_context 构造的 metadata_filter 形如
{"kb_id": "rag_test_kb", "doc_type": "financial"}，Chroma 校验要求
顶层多键必须包在单一算子内，否则抛
ValueError: Expected where to have exactly one operator → /rag/ask 500。

修复点：knowledge_store.normalize_where —— 所有传给 Chroma 的
filter/where 在此归一化，其余模块（hybrid/retrievers fallback）
继续用 flat dict。
"""
import pytest

from backend.rag.vectorstore.knowledge_store import normalize_where


class TestNormalizeWhere:
    def test_none_passthrough(self):
        assert normalize_where(None) is None

    def test_empty_dict_passthrough(self):
        assert normalize_where({}) == {}

    def test_single_key_flat_unchanged(self):
        f = {"kb_id": "rag_test_kb"}
        assert normalize_where(f) == {"kb_id": "rag_test_kb"}

    def test_single_operator_key_unchanged(self):
        f = {"$or": [{"kb_id": "a"}, {"kb_id": "b"}]}
        assert normalize_where(f) == f

    def test_multi_flat_keys_wrapped_with_and(self):
        f = {"kb_id": "rag_test_kb", "doc_type": "financial"}
        out = normalize_where(f)
        assert out == {
            "$and": [{"kb_id": "rag_test_kb"}, {"doc_type": "financial"}]
        }

    def test_three_flat_keys_wrapped(self):
        f = {"kb_id": "k", "doc_type": "legal", "business_domain": "supplier"}
        out = normalize_where(f)
        assert len(out["$and"]) == 3
        assert {"doc_type": "legal"} in out["$and"]

    def test_or_plus_flat_key_wrapped(self):
        # {"$or": [...], "kb_id": ...} 顶层两键（其一为算子）同样非法
        f = {"$or": [{"kb_id": "a"}], "doc_type": "policy"}
        out = normalize_where(f)
        assert "$and" in out and len(out["$and"]) == 2

    def test_chroma_accepts_normalized_multi_filter(self):
        """用真实 Chroma 校验函数验证归一化结果可通过 validate_where。"""
        from chromadb.api.types import validate_where

        f = {"kb_id": "rag_test_kb", "doc_type": "financial"}
        # 未归一化应抛错（缺陷复现）
        with pytest.raises(ValueError):
            validate_where(f)
        # 归一化后通过
        validate_where(normalize_where(f))
