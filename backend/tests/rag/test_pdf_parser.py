"""test_pdf_parser.py — PdfParser 单元测试。

覆盖：
1. 基本加载：2 页 PDF 产出 ≥ 2 个 leaf
2. 空白页处理：全空白 PDF 产出 0 个 leaf，不抛异常
3. 段落合并：相邻短段合并为单个 leaf
4. 容错可观测：单页解析失败 → 汇总 log
"""
import logging
from unittest.mock import patch

import pymupdf as fitz
import pytest

from backend.rag.preprocessing.ast import walk
from backend.rag.preprocessing.parser.pdf_parser import PdfParser


@pytest.fixture
def sample_pdf(tmp_path):
    """创建 2 页 PDF，每页 2 段。"""
    p = tmp_path / "sample.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((50, 50), "第一段文字内容。")
    page1.insert_text((50, 80), "第二段继续描述。")
    page2 = doc.new_page()
    page2.insert_text((50, 50), "第三页内容。")
    doc.save(str(p))
    doc.close()
    return str(p)


def test_pdf_parser_basic_load(sample_pdf):
    ast = PdfParser().parse(sample_pdf)
    assert ast.source_file == sample_pdf
    assert ast.raw_text != ""
    leaves = [n for n in walk(ast.root) if n.type in ("paragraph", "list", "table")]
    assert len(leaves) >= 2


def test_pdf_parser_empty_pages_skipped(tmp_path):
    p = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()  # 全空白页
    doc.save(str(p))
    doc.close()
    ast = PdfParser().parse(str(p))
    leaves = [n for n in walk(ast.root) if n.type in ("paragraph", "list", "table")]
    assert leaves == []


def test_pdf_parser_paragraph_merge(tmp_path):
    """验证连续短段合并为单个 leaf（避免过碎切分）。

    注：PyMuPDF 的 insert_text y 间距映射到 bbox 间距 ≈ 1:1（字符高度 ~15）。
    用 ASCII 避免中文字体编码问题；段B y=120 确保 bbox 间距 > 阈值 → 分隔。
    """
    p = tmp_path / "merge.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "ParaA-line1")
    page.insert_text((50, 65), "ParaA-line2")
    page.insert_text((50, 120), "ParaB alone")
    doc.save(str(p))
    doc.close()
    ast = PdfParser().parse(str(p))
    leaves = [n.text for n in walk(ast.root) if n.type == "paragraph"]
    # ParaA 两行合并为 1 个 leaf
    assert any("ParaA-line1" in t and "ParaA-line2" in t for t in leaves)
    # ParaB 独立成另一段
    assert any("ParaB alone" in t for t in leaves)
    # 总 leaf 数：ParaA 合并为 1 + ParaB 1 = 2（不是 3）
    assert len(leaves) == 2


def test_pdf_parser_reports_skipped_pages(tmp_path, caplog):
    """单页解析失败 → parse 完成后 log 汇总跳过页数（可观测）。"""
    caplog.set_level(logging.WARNING)
    p = tmp_path / "broken.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(p))
    doc.close()

    # monkeypatch 让所有页的 get_text 抛异常 → 全部跳过
    with patch.object(
        fitz.Page, "get_text", side_effect=RuntimeError("simulated parse failure")
    ):
        ast = PdfParser().parse(str(p))
        leaves = [n for n in walk(ast.root) if n.type == "paragraph"]
        assert leaves == []  # 所有页都失败 → 0 个 leaf

    # 验证日志包含「跳过 N 页」汇总
    summary_logs = [
        rec for rec in caplog.records
        if "跳过" in rec.message and "页" in rec.message
    ]
    assert len(summary_logs) >= 1
    assert "1" in summary_logs[0].message  # 跳过 1 页
