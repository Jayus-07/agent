"""MarkdownParser — 用 #/##/### 直接识别标题层级。

Phase 2：先尝试 Q/A 识别，命中则走 FAQ 路径产 qa_question/qa_answer 节点。
"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser._qa_patterns import (
    extract_qa_pairs, looks_like_qa_doc,
)
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+)$")


class MarkdownParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)

        # Phase 2：识别 FAQ 文档 → 整篇按 Q/A 切，不走普通 heading 路径
        if looks_like_qa_doc(raw):
            pairs = extract_qa_pairs(raw)
            for q, a, _ptype in pairs:
                root.children.append(
                    DocumentNode(type="qa_question", text=q)
                )
                root.children.append(
                    DocumentNode(type="qa_answer", text=a)
                )
            logger.info(
                f"[MarkdownParser] {file_path} 识别为 FAQ 文档，"
                f"产出 {len(pairs)} 个 Q/A 对"
            )
            return DocumentAST(root=root, source_file=file_path, raw_text=raw)

        # 普通文档：heading 解析
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
