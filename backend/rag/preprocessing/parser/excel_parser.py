"""ExcelParser — 占位，Phase 3 实现。"""
from __future__ import annotations

from backend.rag.preprocessing.ast import DocumentAST
from backend.rag.preprocessing.parser.base import BaseDocumentParser


class ExcelParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        raise NotImplementedError("Excel 解析器待实现")
