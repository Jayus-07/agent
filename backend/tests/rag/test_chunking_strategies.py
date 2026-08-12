# backend/tests/rag/test_chunking_strategies.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import StructureChunkStrategy, RecursiveChunkStrategy

AST = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="section", text="售后制度", level=1, children=[
            DocumentNode(type="section", text="退货流程", level=2, children=[
                DocumentNode(type="paragraph", text="客服审核退货原因。"),
            ]),
            DocumentNode(type="section", text="差评处理", level=2, children=[
                DocumentNode(type="paragraph", text="48小时内给出方案。"),
            ]),
        ]),
    ]),
    source_file="a.md",
    raw_text="",
)


def test_structure_strategy_produces_parent_and_leaf():
    chunks = StructureChunkStrategy().split(AST, "a.md")
    granules = {c.metadata["granularity"] for c in chunks}
    assert granules == {"leaf", "parent"}
    # 每个 leaf 有 parent_chunk_id + section_path
    leaf = next(c for c in chunks if c.metadata["granularity"] == "leaf")
    assert leaf.metadata["parent_chunk_id"]
    assert leaf.metadata["section_path"]


def test_leaf_links_to_its_section_parent():
    chunks = StructureChunkStrategy().split(AST, "a.md")
    parents = {c.metadata["chunk_id"]: c for c in chunks if c.metadata["granularity"] == "parent"}
    for c in chunks:
        if c.metadata["granularity"] == "leaf":
            pid = c.metadata["parent_chunk_id"]
            assert pid in parents
            assert c.metadata["section_path"] == parents[pid].metadata["section_path"]


def test_recursive_strategy_splits_long_leaf():
    long_text = "这是一个没有结构的长段落。" * 200
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=long_text),
        ]),
        raw_text=long_text,
    )
    chunks = RecursiveChunkStrategy().split(ast, "b.txt")
    assert len(chunks) > 1
