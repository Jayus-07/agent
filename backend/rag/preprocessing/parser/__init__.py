"""Format Parser 层 — 按扩展名分发到对应解析器。"""
from __future__ import annotations

import os

from backend.rag.preprocessing.ast import DocumentAST, annotate_ast_provenance
from backend.rag.preprocessing.parser.csv_parser import CsvParser
from backend.rag.preprocessing.parser.docx_parser import DocxParser
from backend.rag.preprocessing.parser.excel_parser import ExcelParser
from backend.rag.preprocessing.parser.html_parser import HtmlParser
from backend.rag.preprocessing.parser.json_parser import JsonParser
from backend.rag.preprocessing.parser.markdown_parser import MarkdownParser
from backend.rag.preprocessing.parser.pdf_parser import PdfParser
from backend.rag.preprocessing.parser.pptx_parser import PptxParser
from backend.rag.preprocessing.parser.xml_parser import XmlParser
from backend.rag.preprocessing.parser.txt_parser import TxtParser

_PARSERS = {
    ".md": MarkdownParser,
    ".markdown": MarkdownParser,
    ".txt": TxtParser,
    ".pdf": PdfParser,
    ".docx": DocxParser,
    ".xlsx": ExcelParser,
    ".csv": CsvParser,
    ".html": HtmlParser,
    ".htm": HtmlParser,
    ".pptx": PptxParser,
    ".json": JsonParser,
    ".xml": XmlParser,
}

# F6: 单一来源常量 — 已注册解析器的扩展名集合。
# 上游所有白名单（pipeline/indexer/loader/上传路由）都必须从这里派生，
# 避免多处硬编码名单漂移（历史：上传缺 .markdown、loader/indexer 缺 .markdown）。
PARSABLE_EXTS: frozenset[str] = frozenset(_PARSERS.keys())


def parse_file(
    file_path: str, *, ocr_page_budget: int | None = None,
) -> DocumentAST:
    """按扩展名分发解析器，未知扩展回退 TxtParser。"""
    ext = os.path.splitext(file_path)[1].lower()
    parser_cls = _PARSERS.get(ext, TxtParser)
    if ext == ".pdf" and ocr_page_budget is not None:
        ast = parser_cls().parse(
            file_path, ocr_page_budget=ocr_page_budget,
        )
    else:
        ast = parser_cls().parse(file_path)
    if ext == ".htm":
        ast.source_format = "html"
    annotate_ast_provenance(ast, parser_cls.PARSER_VERSION)
    return ast
