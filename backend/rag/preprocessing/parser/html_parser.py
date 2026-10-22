"""HTML 解析器：保留正文 DOM 层级、列表、表格和原始 DOM 定位。"""
from __future__ import annotations

from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_BLOCK_TAGS = {
    "address", "article", "blockquote", "div", "main", "p", "pre",
}
_SKIP_TAGS = {"nav", "header", "footer", "aside", "script", "style", "noscript"}
_SKIP_ROLES = {"navigation", "banner", "complementary"}
_SKIP_CLASS_TOKENS = {
    "ad", "ads", "advert", "advertisement", "banner", "sponsor",
}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


def _bounded_int(value: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _read_html(path: str) -> str:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class _HtmlTreeBuilder(HTMLParser):
    def __init__(self, source_file: str):
        super().__init__(convert_charrefs=True)
        self.source_file = source_file
        self.root = DocumentNode(type="section", text="", level=0)
        self.sections = [self.root]
        self.path: list[tuple[str, int]] = []
        self.child_counts: dict[tuple[tuple[str, ...], str], int] = defaultdict(int)
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.buffer: list[str] = []
        self.buffer_kind = "paragraph"
        self.buffer_path = ""
        self.buffer_line = 0
        self.table_path = ""
        self.table_rows: list[list[str]] = []
        self.table_cells: list[tuple[str, int, int]] = []
        self.cell_parts: list[str] | None = None
        self.cell_colspan = 1
        self.cell_rowspan = 1
        self.rowspan_values: dict[int, tuple[str, int]] = {}
        self.issues: list[dict] = []

    def _path(self) -> str:
        return "/" + "/".join(f"{tag}[{index}]" for tag, index in self.path)

    def _push_path(self, tag: str) -> None:
        parent = tuple(item[0] for item in self.path)
        key = (parent, tag)
        self.child_counts[key] += 1
        self.path.append((tag, self.child_counts[key]))

    def _pop_path(self, tag: str) -> None:
        for index in range(len(self.path) - 1, -1, -1):
            if self.path[index][0] == tag:
                del self.path[index:]
                return

    def _flush(self) -> None:
        text = " ".join("".join(self.buffer).split())
        self.buffer.clear()
        if not text:
            return
        node = DocumentNode(type=self.buffer_kind, text=text)
        node.source_locator = {
            "dom_path": self.buffer_path,
            "line": self.buffer_line,
        }
        self.sections[-1].children.append(node)

    def _finish_row(self) -> None:
        row: list[str] = []
        col = 0
        cell_index = 0
        while cell_index < len(self.table_cells) or col in self.rowspan_values:
            carried = self.rowspan_values.pop(col, None)
            if carried is not None:
                value, remaining = carried
                row.append(value)
                if remaining > 1:
                    self.rowspan_values[col] = (value, remaining - 1)
                col += 1
                continue
            if cell_index >= len(self.table_cells):
                break
            value, colspan, rowspan = self.table_cells[cell_index]
            cell_index += 1
            for _ in range(colspan):
                row.append(value)
                if rowspan > 1:
                    self.rowspan_values[col] = (value, rowspan - 1)
                col += 1
        self.table_rows.append(row)
        self.table_cells.clear()

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        self._push_path(tag)
        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth += 1
            else:
                self._pop_path(tag)
            return
        if tag in _SKIP_TAGS:
            self.skip_depth = 1
            return
        role = attributes.get("role", "").strip().lower()
        if role in _SKIP_ROLES:
            self.skip_depth = 1
            return
        markers = re.split(
            r"[\s_-]+",
            f"{attributes.get('class', '')} {attributes.get('id', '')}".lower(),
        )
        if any(marker in _SKIP_CLASS_TOKENS for marker in markers if marker):
            self.skip_depth = 1
            return
        if tag == "title":
            self.in_title = True
            return
        if tag == "table":
            self._flush()
            self.table_rows = []
            self.rowspan_values = {}
            self.table_path = self._path()
            return
        if tag == "tr":
            self.table_cells = []
            return
        if tag in {"td", "th"}:
            self.cell_parts = []
            self.cell_colspan = _bounded_int(attributes.get("colspan"), 1, 32)
            self.cell_rowspan = _bounded_int(attributes.get("rowspan"), 1, 1000)
            return
        if tag == "img":
            alt = attributes.get("alt", "").strip()
            if alt:
                node = DocumentNode(type="figure", text=f"图片说明：{alt}")
                node.source_locator = {"dom_path": self._path(), "has_alt": True}
                self.sections[-1].children.append(node)
            else:
                self.issues.append({
                    "code": "html_image_without_alt",
                    "severity": "warn",
                    "detail": "图片没有 alt 文本，未将图片内容当作已解析事实",
                    "source_locator": {"dom_path": self._path()},
                })
            self._pop_path(tag)
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self.buffer_kind = "paragraph"
            self.buffer_path = self._path()
            self.buffer_line = self.getpos()[0]
            self._heading_level = int(tag[1])
            self._heading_attrs = {"dom_path": self.buffer_path, "line": self.buffer_line}
            self._heading_parts = []
            return
        if tag == "li":
            self._flush()
            self.buffer_kind = "list"
            self.buffer_path = self._path()
            self.buffer_line = self.getpos()[0]
            return
        if tag == "pre":
            self._flush()
            self.buffer_kind = "code"
            self.buffer_path = self._path()
            self.buffer_line = self.getpos()[0]
            return
        if tag in _BLOCK_TAGS or tag in {"ul", "ol", "br", "hr"}:
            self._flush()
            self.buffer_kind = "paragraph"
            self.buffer_path = self._path()
            self.buffer_line = self.getpos()[0]
        if tag in _VOID_TAGS:
            self._pop_path(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth = max(0, self.skip_depth - 1)
            self._pop_path(tag)
            return
        if tag == "title":
            self.in_title = False
        elif tag in {"td", "th"} and self.cell_parts is not None:
            value = " ".join("".join(self.cell_parts).split())
            self.table_cells.append((value, self.cell_colspan, self.cell_rowspan))
            self.cell_parts = None
        elif tag == "tr":
            self._finish_row()
        elif tag == "table":
            from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text

            if self.table_rows:
                table_text = make_table_chunk_text(self.table_rows)
                node = DocumentNode(
                    type="table", text=table_text, rows=self.table_rows,
                )
                node.source_locator = {
                    "dom_path": self.table_path,
                    "row_start": 1,
                    "row_end": max(0, len(self.table_rows) - 1),
                    "columns": [str(value) for value in self.table_rows[0]][:64],
                }
                self.sections[-1].children.append(node)
            self.table_rows = []
            self.table_path = ""
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = " ".join("".join(self._heading_parts).split())
            if text:
                level = self._heading_level
                while len(self.sections) > 1 and self.sections[-1].level >= level:
                    self.sections.pop()
                section = DocumentNode(type="section", text=text, level=level)
                section.source_locator = dict(self._heading_attrs)
                self.sections[-1].children.append(section)
                self.sections.append(section)
            self._heading_parts = None
            self._heading_level = None
        elif tag in {"li", "pre"} or tag in _BLOCK_TAGS:
            self._flush()
            self.buffer_kind = "paragraph"
            self.buffer_path = self._path()
            self.buffer_line = self.getpos()[0]
        self._pop_path(tag)

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if getattr(self, "_heading_level", None) is not None:
            self._heading_parts.append(data)
            return
        if self.cell_parts is not None:
            self.cell_parts.append(data)
            return
        if not self.buffer:
            self.buffer_path = self._path()
            self.buffer_line = self.getpos()[0]
        self.buffer.append(data)


class HtmlParser(BaseDocumentParser):
    """HTML 解析器：排除页面装饰，按标题、列表和表格生成 AST。"""

    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        builder = _HtmlTreeBuilder(file_path)
        builder.feed(_read_html(file_path))
        builder.close()
        builder._flush()
        title = " ".join("".join(builder.title_parts).split())
        raw_lines: list[str] = []

        def collect(node: DocumentNode) -> None:
            if node.text:
                raw_lines.append(node.text)
            for child in node.children:
                collect(child)

        collect(builder.root)
        raw_text = "\n".join(raw_lines)
        return DocumentAST(
            root=builder.root,
            source_file=file_path,
            raw_text=raw_text,
            document_title=title,
            parse_issues=builder.issues,
            parse_status=(
                "partial" if any(i.get("severity") == "error" for i in builder.issues)
                else "complete"
            ),
            source_format="html",
            parser_version=self.PARSER_VERSION,
        )
