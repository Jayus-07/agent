"""MarkdownParser — 用 #/##/### 直接识别标题层级。"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+)$")


class MarkdownParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)
        stack: list[DocumentNode] = [root]
        buf: list[str] = []          # 当前段落缓冲（空行或标题时 flush）

        def _flush():
            if buf:
                stack[-1].children.append(
                    DocumentNode(type="paragraph", text="\n".join(buf)))
                buf.clear()

        for line in raw.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                _flush()
                level = len(m.group(1))
                title = m.group(2).strip()
                node = DocumentNode(type="section", text=title, level=level)
                while len(stack) > 1 and stack[-1].level >= level:
                    stack.pop()
                stack[-1].children.append(node)
                stack.append(node)
                continue
            li = _LIST_RE.match(line)
            if li:
                _flush()
                stack[-1].children.append(DocumentNode(type="list", text=li.group(1).strip()))
                continue
            if not line.strip():
                _flush()
                continue
            buf.append(line.strip())

        _flush()
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
