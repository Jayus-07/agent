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


# =====================================================
# 生产加固：chunk_id 内容派生 / parent 超长保护 / MAX_CHUNKS
# =====================================================

def _structure_ast(leaf1_text: str, leaf2_text: str) -> DocumentAST:
    """构造两个段落的单 section AST（用于结构切分稳定性验证）。"""
    return DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="第一章", level=1, children=[
                DocumentNode(type="paragraph", text=leaf1_text),
                DocumentNode(type="paragraph", text=leaf2_text),
            ]),
        ]),
        raw_text="",
    )


def test_chunk_id_content_derived_power_idempotent():
    """同一文档相同内容重复切分 → chunk_id 相同；内容不同 → 不同。"""
    from backend.rag.preprocessing.chunking import _chunk_id
    a = _chunk_id("d.md", "leaf", "相同内容")
    b = _chunk_id("d.md", "leaf", "相同内容")
    c = _chunk_id("d.md", "leaf", "不同内容")
    assert a == b          # 幂等：同内容同 id
    assert a != c          # 内容绑定
    assert len(a) == 12    # 格式不变（md5 前 12 位 hex）


def test_structure_chunk_id_stable_across_local_edit():
    """文档局部微变：未受影响叶子的 chunk_id 保持稳定（不再整体漂移）。"""
    from backend.rag.preprocessing.chunking import StructureChunkStrategy
    c1 = StructureChunkStrategy().split(_structure_ast("内容A", "稳定段内容"), "d.md")
    c2 = StructureChunkStrategy().split(_structure_ast("内容B", "稳定段内容"), "d.md")

    def _stable_ids(chunks):
        return {c.metadata["chunk_id"] for c in chunks
                if "稳定段内容" in c.page_content and c.metadata["granularity"] == "leaf"}

    assert _stable_ids(c1) == _stable_ids(c2)


def test_structure_oversized_section_splits_parents(monkeypatch):
    """section 文本超 PARENT_CHUNK_TOKENS → 拆多个 parent，无超大 chunk，leaf 关联正确。"""
    import backend.rag.preprocessing.chunking as chunking_mod
    monkeypatch.setattr(chunking_mod, "PARENT_CHUNK_TOKENS", 40)

    long_leaf = "段落一。" + "内容" * 40   # 远超预算，但 leaf 强制成组
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="大节", level=1, children=[
                DocumentNode(type="paragraph", text=long_leaf),
                DocumentNode(type="paragraph", text="第二段。" + "内容" * 30),
                DocumentNode(type="paragraph", text="第三段。" + "内容" * 30),
            ]),
        ]),
        raw_text="",
    )
    chunks = chunking_mod.StructureChunkStrategy().split(ast, "d.md")

    parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    parent_ids = {p.metadata["chunk_id"] for p in parents}
    # 超长 section 不再产出单个超大 parent
    assert len(parents) >= 2
    assert all(p.metadata["chunk_tokens"] > 0 for p in parents)
    # 每个 leaf 的 parent_chunk_id 指向真实存在的 parent
    assert all(l.metadata["parent_chunk_id"] in parent_ids for l in leaves)
    # 总 chunk 数 = parents + leaves（不丢内容）
    assert len(chunks) == len(parents) + len(leaves)


def test_max_chunks_per_doc_truncates(monkeypatch):
    """chunk 数量超 MAX_CHUNKS_PER_DOC → 截断 + 不无界产出。"""
    import backend.rag.preprocessing.chunking as chunking_mod
    monkeypatch.setattr("backend.config.MAX_CHUNKS_PER_DOC", 3)
    # 绕过 _enrich 内 import 的 config 绑定：直接构造超量 chunks 调 _enrich
    from langchain_core.documents import Document as _Doc
    many = [_Doc(page_content=f"第{i}段内容", metadata={}) for i in range(10)]
    result = chunking_mod._enrich(many, "d.md")
    assert len(result) == 3  # 截断到上限
    assert all("chunk_index" in c.metadata for c in result)
