"""TxtParser — 用空行 + 编号规则识别结构。"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_NUM_HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百千\d]+[章节条]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.)]?)\s*(.+)$")


class TxtParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)
        buf: list[str] = []

        def _flush():
            if buf:
                root.children.append(DocumentNode(type="paragraph", text="\n".join(buf)))
                buf.clear()

        for line in raw.split("\n"):
            m = _NUM_HEADING_RE.match(line.strip())
            if m and len(line.strip()) <= 60:
                _flush()
                root.children.append(DocumentNode(type="section", text=m.group(1).strip(), level=1))
                continue
            if not line.strip():
                _flush()
                continue
            buf.append(line.strip())

        _flush()
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
