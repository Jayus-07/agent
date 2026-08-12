# backend/tests/rag/test_chunking_strategies.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    StructureChunkStrategy, RecursiveChunkStrategy, QAChunkStrategy,
)

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


def test_structure_leaves_not_duplicated_across_sections():
    chunks = StructureChunkStrategy().split(AST, "a.md")
    leaf_texts = [c.page_content for c in chunks if c.metadata["granularity"] == "leaf"]
    # 每个段落叶子只出现一次（不因嵌套层级在每个祖先 section 重复产出）
    assert leaf_texts.count("客服审核退货原因。") == 1
    assert leaf_texts.count("48小时内给出方案。") == 1


QA_AST = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="qa_question", text="怎么退货？"),
        DocumentNode(type="qa_answer", text="提交申请后客服审核。"),
    ]),
    raw_text="",
)


def test_all_strategies_emit_metadata_protocol():
    cases = [
        (StructureChunkStrategy(), AST),
        (RecursiveChunkStrategy(), AST),
        (QAChunkStrategy(), QA_AST),
    ]
    for strategy, ast in cases:
        chunks = strategy.split(ast, "a.md")
        for c in chunks:
            for key in ("chunk_id", "chunk_tokens"):
                assert key in c.metadata, f"{strategy.__class__.__name__} 缺 {key}"
            if c.metadata["granularity"] == "leaf":
                for key in ("parent_chunk_id", "section_path",
                            "section_title", "section_level"):
                    assert key in c.metadata, f"{strategy.__class__.__name__} leaf 缺 {key}"


def test_sibling_sections_with_same_title_have_unique_chunk_ids():
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="常见问题", level=1, children=[
                DocumentNode(type="paragraph", text="问题A。"),
            ]),
            DocumentNode(type="section", text="常见问题", level=1, children=[
                DocumentNode(type="paragraph", text="问题B。"),
            ]),
        ]),
        raw_text="",
    )
    chunks = StructureChunkStrategy().split(ast, "a.md")
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))


def test_parent_chunk_includes_section_title():
    """parent chunk 的 page_content 必须包含 section 自身标题（如「退货流程」）。"""
    chunks = StructureChunkStrategy().split(AST, "a.md")
    parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
    assert any("退货流程" in c.page_content for c in parents)


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
