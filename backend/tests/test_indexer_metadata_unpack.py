"""守住 detect_business_domain 返回 3 元组的契约 + indexer 解包正确性。

背景:P1 bug — indexer._build_doc_metadata 用 `domain, domain_detail = ...` 解包
detect_business_domain 的 return_detail=True 返回值,但该函数实际返回 3 元组
(primary, alternatives, detail),导致 too many values to unpack (expected 2)。

修复后:indexer 解包 3 个值,domain_detail 仍为 dict。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer


# ============ detect_business_domain 契约 ============

class TestDetectBusinessDomainContract:
    """detect_business_domain 在 return_detail=True 时必须返回 3 元组。"""

    def test_return_detail_true_returns_three_tuple(self):
        from backend.rag.preprocessing.metadata import detect_business_domain

        result = detect_business_domain(
            "跨境电商 Amazon 平台 Amazon Listing 标题 SOP 标准操作流程 库存管理",
            return_detail=True,
        )
        assert isinstance(result, tuple)
        assert len(result) == 3, (
            f"detect_business_domain(return_detail=True) 必须返回 (primary, alternatives, detail) "
            f"3 元组,实际 {len(result)} 个 — 这会导致 indexer too many values to unpack"
        )

    def test_return_detail_false_returns_two_tuple(self):
        from backend.rag.preprocessing.metadata import detect_business_domain

        result = detect_business_domain(
            "跨境电商 Amazon 平台 Amazon Listing 标题 SOP", return_detail=False,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_return_detail_default_returns_two_tuple(self):
        from backend.rag.preprocessing.metadata import detect_business_domain

        # 默认 return_detail=False
        result = detect_business_domain("跨境电商 Amazon")
        assert len(result) == 2


# ============ indexer._build_doc_metadata 解包 ============

class TestIndexerUnpacksDomainTuple:
    """_build_doc_metadata 必须正确解包 3 元组,不抛 too many values to unpack。"""

    def test_build_doc_metadata_does_not_raise_on_pdf(self, tmp_path, monkeypatch):
        """用真实 PDF 触发 _build_doc_metadata,验证能完整跑完不抛 unpack 错。"""
        from pathlib import Path
        pdf_path = Path("backend/data/docs/rag_test_kb/general/03_库存管理制度.pdf")
        if not pdf_path.exists():
            pytest.skip("PDF 不存在,跳过集成测试")

        idx = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=MagicMock(),
        )
        # 用真实 PDF 文本作为 full_text
        full_text = pdf_path.read_bytes().decode("utf-8", errors="replace")[:2000]
        try:
            result = asyncio.run(idx._build_doc_metadata(
                full_text,
                base_meta={"source_file": "03_库存管理制度.pdf", "file_path": str(pdf_path)},
            ))
        except ValueError as e:
            if "too many values to unpack" in str(e):
                pytest.fail(
                    f"P1:indexer._build_doc_metadata 仍然抛 unpack 错: {e}"
                )
            raise
        # 成功路径(可能 fallback general,只要不抛就 OK)
        assert result is not None
        assert "doc_type" in result