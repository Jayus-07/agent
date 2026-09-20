"""统一 Document AST — 切分流水线的中间表示。

Parser 产出 Raw AST（格式级结构），Structure Analyzer 归一化为 Normalized AST，
所有 ChunkStrategy 消费 Normalized AST。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

VALID_NODE_TYPES = {
    "heading", "section", "paragraph", "list", "table",
    "qa_question", "qa_answer",
}

LEAF_TYPES = {"paragraph", "list", "table", "qa_question", "qa_answer"}


@dataclass
class DocumentNode:
    """AST 节点。section 是容器（text=标题，children=子节点），leaf 类型是叶子。"""
    type: str
    text: str
    level: int = 0
    children: list["DocumentNode"] = field(default_factory=list)
    rows: list[list[str]] | None = None       # table 专用
    source_range: tuple[int, int] = (0, 0)    # (start, end) 在 raw_text 中的偏移
    # R-P0-3 原文可追溯：清洗前原文 + 清洗操作留痕（字段追加在尾部，位置构造兼容）
    raw_text: str = ""                        # 清洗前原始文本；未清洗时为空串（等价 text）
    cleaning_operations: list[str] = field(default_factory=list)  # DocumentCleaner 执行的操作清单
    # §5.2 原文可追溯（2026-09-17 追加，位置构造兼容）：页码与版面坐标
    # page_number：1-based 页码，非分页格式（MD/TXT 等）恒 0；bbox：PDF 版面
    # (x0, y0, x1, y1)，仅 PdfParser 填写，其他解析器为空元组
    page_number: int = 0
    bbox: tuple = ()


@dataclass
class DocumentAST:
    """整棵文档结构树。root 是虚拟根（type="section", level=0, text=""）。"""
    root: DocumentNode
    source_file: str = ""
    raw_text: str = ""
    # §5.1 质量记录：扫描件 OCR 需求和执行状态（PdfParser 写入，下游留痕用）。
    # ``ocr_triggered`` 只表示有页面实际产出 OCR 文本；``ocr_required``
    # 表示文本层不足，即使 OCR 关闭/不可用也必须保留这个事实。
    ocr_required: bool = False
    ocr_attempted: bool = False
    ocr_triggered: bool = False
    ocr_pages: int = 0  # 实际产出文本的 OCR 页数（失败页不计）


def walk(node: DocumentNode) -> Iterator[DocumentNode]:
    """DFS 先序遍历所有节点。"""
    yield node
    for child in node.children:
        yield from walk(child)


def iter_sections(ast: DocumentAST) -> Iterator[tuple[DocumentNode, list[str]]]:
    """为每个 section 节点产出 (node, 祖先标题链)，链不含虚拟根。"""
    def _dfs(node: DocumentNode, path: list[str]):
        for child in node.children:
            if child.type == "section":
                child_path = path + [child.text]
                yield child, child_path
                yield from _dfs(child, child_path)
            else:
                yield from _dfs(child, path)

    yield from _dfs(ast.root, [])
