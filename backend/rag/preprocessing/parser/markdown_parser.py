"""MarkdownParser — 用 #/##/### 直接识别标题层级。

P0-01（2026-10-13）：不再整篇 FAQ 转换。一律走 heading/段落解析（结构零
丢失），再由 annotate_qa_nodes 做局部 Q/A 配对——命中段落转 qa 节点，
其余章节/段落原样保留，混合文档（FAQ+正文）不再丢内容。
"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser._qa_patterns import annotate_qa_nodes
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

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
        qa_pairs = annotate_qa_nodes(root)
        if qa_pairs:
            logger.info(
                f"[MarkdownParser] {file_path} 局部识别 {qa_pairs} 个 Q/A 对"
                f"（其余结构保留）"
            )
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
