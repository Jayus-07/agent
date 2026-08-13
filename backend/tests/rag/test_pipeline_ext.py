"""test_pipeline_ext.py — parser 注册 + pipeline 扩展名白名单。

覆盖：
1. _PARSERS 字典含 .pdf / .docx
2. _SUPPORTED_EXTS 含 .pdf / .docx
3. 未知扩展 → parse_and_chunk 返回 [] + log warning
"""
import logging

import pytest

from backend.rag.preprocessing.parser import _PARSERS
from backend.rag.preprocessing.pipeline import (
    _SUPPORTED_EXTS, parse_and_chunk,
)


def test_parser_dispatch_pdf_docx_registered():
    """验证 .pdf / .docx 在 _PARSERS 字典里。"""
    assert ".pdf" in _PARSERS
    assert ".docx" in _PARSERS


def test_pipeline_supported_exts_includes_pdf_docx():
    """验证 _SUPPORTED_EXTS 包含 .pdf / .docx。"""
    assert ".pdf" in _SUPPORTED_EXTS
    assert ".docx" in _SUPPORTED_EXTS


def test_parse_and_chunk_unsupported_ext_returns_empty(tmp_path, caplog):
    """未知扩展名 → parse_and_chunk 返回 []，且 log warning。"""
    caplog.set_level(logging.WARNING)
    fake = tmp_path / "fake.xyz"
    fake.write_text("dummy", encoding="utf-8")
    chunks = parse_and_chunk(str(fake))
    assert chunks == []
    assert any(
        "暂不支持" in rec.message or "skip" in rec.message.lower()
        for rec in caplog.records
    )
