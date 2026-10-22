"""MarkdownParser — 标题、列表、代码块、表格与段落结构解析。

P0-01（2026-10-13）：不再整篇 FAQ 转换。一律走 heading/段落解析（结构零
丢失），再由 annotate_qa_nodes 做局部 Q/A 配对——命中段落转 qa 节点，
其余章节/段落原样保留，混合文档（FAQ+正文）不再丢内容。

2026-10-22：补齐 fenced code 与 pipe table 节点；代码块归属其所在章节，
表格保留表头、行和原始行号定位，不再退化为普通段落。
"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser._qa_patterns import annotate_qa_nodes
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(?:[^\n]*)$")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _split_table_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_cells(line)
    return bool(cells) and all(
        _TABLE_SEPARATOR_CELL_RE.match(cell) is not None for cell in cells
    )


class MarkdownParser(BaseDocumentParser):
    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        lines = raw.split("\n")
        offsets: list[int] = []
        cursor = 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line) + 1

        root = DocumentNode(type="section", text="", level=0)
        stack: list[DocumentNode] = [root]
        buf: list[str] = []          # 当前段落缓冲（空行/标题/代码/表格时 flush）
        issues: list[dict] = []

        def _flush():
            if buf:
                stack[-1].children.append(
                    DocumentNode(type="paragraph", text="\n".join(buf)))
                buf.clear()

        def _span(start: int, end: int) -> tuple[int, int]:
            start = max(0, min(start, len(lines) - 1))
            end = max(start, min(end, len(lines) - 1))
            return offsets[start], offsets[end] + len(lines[end])

        i = 0
        while i < len(lines):
            line = lines[i]

            fence = _FENCE_RE.match(line)
            if fence:
                _flush()
                fence_token = fence.group(1)
                start = i
                body: list[str] = []
                i += 1
                closed = False
                while i < len(lines):
                    if re.match(
                        rf"^\s*{re.escape(fence_token)}\s*$", lines[i],
                    ):
                        closed = True
                        break
                    body.append(lines[i])
                    i += 1
                end = i if closed else len(lines) - 1
                node = DocumentNode(type="code", text="\n".join(body).strip())
                node.source_range = _span(start, end)
                node.source_locator = {
                    "line_start": start + 1,
                    "line_end": end + 1,
                    "kind": "fenced_code",
                }
                stack[-1].children.append(node)
                if not closed:
                    issues.append({
                        "code": "markdown_unclosed_code_fence",
                        "severity": "warn",
                        "detail": "代码围栏未闭合，后续内容按代码块处理",
                    })
                i += 1
                continue

            if (
                "|" in line
                and i + 1 < len(lines)
                and _is_table_separator(lines[i + 1])
            ):
                header = _split_table_cells(line)
                if header:
                    _flush()
                    start = i
                    rows = [header]
                    i += 2
                    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                        cells = _split_table_cells(lines[i])
                        if len(cells) < len(header):
                            cells.extend([""] * (len(header) - len(cells)))
                        rows.append(cells[:len(header)])
                        i += 1
                    from backend.rag.preprocessing.parser._table_nl import (
                        make_table_chunk_text,
                    )

                    table_text = make_table_chunk_text(rows)
                    node = DocumentNode(
                        type="table", text=table_text, rows=rows,
                    )
                    node.source_range = _span(start, i - 1)
                    node.source_locator = {
                        "line_start": start + 1,
                        "line_end": i,
                        "row_start": 1,
                        "row_end": max(0, len(rows) - 1),
                        "columns": [str(value) for value in header][:64],
                        "kind": "pipe_table",
                    }
                    stack[-1].children.append(node)
                    continue

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
                i += 1
                continue

            li = _LIST_RE.match(line)
            if li:
                _flush()
                stack[-1].children.append(
                    DocumentNode(type="list", text=li.group(1).strip()))
                i += 1
                continue

            if not line.strip():
                _flush()
                i += 1
                continue

            buf.append(line.strip())
            i += 1

        _flush()
        qa_pairs = annotate_qa_nodes(root)
        if qa_pairs:
            logger.info(
                f"[MarkdownParser] {file_path} 局部识别 {qa_pairs} 个 Q/A 对"
                f"（其余结构保留）"
            )
        return DocumentAST(
            root=root,
            source_file=file_path,
            raw_text=raw,
            parse_issues=issues,
            parse_status="partial" if issues else "complete",
            parser_version=self.PARSER_VERSION,
        )
