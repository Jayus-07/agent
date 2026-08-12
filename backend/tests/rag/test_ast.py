# backend/tests/rag/test_ast.py
from backend.rag.preprocessing.ast import (
    DocumentNode, DocumentAST, walk, iter_sections,
)


def _make_tree():
    root = DocumentNode(type="section", text="", level=0)
    ch1 = DocumentNode(type="section", text="售后制度", level=1)
    ch2 = DocumentNode(type="section", text="退货流程", level=2)
    leaf = DocumentNode(type="paragraph", text="客服审核退货原因。")
    ch2.children.append(leaf)
    ch1.children.append(ch2)
    root.children.append(ch1)
    return DocumentAST(root=root, source_file="a.md", raw_text="售后制度\n退货流程\n客服审核退货原因。")


def test_walk_yields_all_nodes():
    ast = _make_tree()
    types = [n.type for n in walk(ast.root)]
    assert types == ["section", "section", "section", "paragraph"]


def test_iter_sections_returns_full_path():
    ast = _make_tree()
    paths = {n.text: path for n, path in iter_sections(ast)}
    assert paths["退货流程"] == ["售后制度", "退货流程"]


def test_node_defaults():
    n = DocumentNode(type="paragraph", text="x")
    assert n.level == 0 and n.children == [] and n.rows is None
