"""TxtParser — 用空行 + 编号规则识别结构。

P0-01（2026-10-13）：不再整篇 FAQ 转换。一律走编号 heading/段落解析，
再由 annotate_qa_nodes 做局部 Q/A 配对——混合文档（FAQ+正文）不再丢内容。
"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser._qa_patterns import annotate_qa_nodes
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_NUM_HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百千\d]+[章节条]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.)]?)\s*(.+)$")


class TxtParser(BaseDocumentParser):
    @staticmethod
    def _read_text(file_path: str) -> str:
        """读取文本文件，utf-8 失败回退 gb18030（P4 修复 2026-10-05）。

        国内历史语料常见 GBK/GB18030 编码；此前硬解 utf-8 直接
        UnicodeDecodeError → parse failed。回退顺序：utf-8（严格）→
        gb18030（中文超集，严格）→ utf-8 errors=replace 兜底。
        """
        raw_bytes = open(file_path, "rb").read()
        for enc in ("utf-8", "gb18030"):
            try:
                return raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        logger.warning(
            f"[TxtParser] {file_path} 编码识别失败（非 utf-8/gb18030），"
            f"以 utf-8 replace 兜底读取")
        return raw_bytes.decode("utf-8", errors="replace")

    def parse(self, file_path: str) -> DocumentAST:
        raw = self._read_text(file_path)

        root = DocumentNode(type="section", text="", level=0)
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
        qa_pairs = annotate_qa_nodes(root)
        if qa_pairs:
            logger.info(
                f"[TxtParser] {file_path} 局部识别 {qa_pairs} 个 Q/A 对"
                f"（其余结构保留）"
            )
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
