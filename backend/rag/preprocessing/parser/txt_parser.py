"""TxtParser — 用空行 + 编号规则识别结构。

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

_NUM_HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百千\d]+[章节条]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.)]?)\s*(.+)$")


class TxtParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)

        # Phase 2：识别 FAQ 文档 → 整篇按 Q/A 切
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
                f"[TxtParser] {file_path} 识别为 FAQ 文档，"
                f"产出 {len(pairs)} 个 Q/A 对"
            )
            return DocumentAST(root=root, source_file=file_path, raw_text=raw)

        # 普通文档：编号 heading 解析
        current: DocumentNode = root
        buf: list[str] = []

        def _flush():
            if buf:
                current.children.append(DocumentNode(type="paragraph", text="\n".join(buf)))
                buf.clear()

        for line in raw.split("\n"):
            m = _NUM_HEADING_RE.match(line.strip())
            if m and len(line.strip()) <= 60:
                _flush()
                current = DocumentNode(type="section", text=m.group(1).strip(), level=1)
                root.children.append(current)
                continue
            if not line.strip():
                _flush()
                continue
            buf.append(line.strip())

        _flush()
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
