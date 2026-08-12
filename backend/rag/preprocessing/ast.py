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


@dataclass
class DocumentAST:
    """整棵文档结构树。root 是虚拟根（type="section", level=0, text=""）。"""
    root: DocumentNode
    source_file: str = ""
    raw_text: str = ""


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
