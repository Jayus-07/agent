"""Format Parser 层 — 按扩展名分发到对应解析器。"""
from __future__ import annotations

import os

from backend.rag.preprocessing.ast import DocumentAST
from backend.rag.preprocessing.parser.markdown_parser import MarkdownParser
from backend.rag.preprocessing.parser.txt_parser import TxtParser

_PARSERS = {
    ".md": MarkdownParser,
    ".markdown": MarkdownParser,
    ".txt": TxtParser,
}


def parse_file(file_path: str) -> DocumentAST:
    """按扩展名分发解析器，未知扩展回退 TxtParser。"""
    ext = os.path.splitext(file_path)[1].lower()
    parser_cls = _PARSERS.get(ext, TxtParser)
    return parser_cls().parse(file_path)
