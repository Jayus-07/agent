"""test_fixed_size_chunking.py — FixedSizeChunkStrategy 固定长度切分。"""
from backend.config import LEAF_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import FixedSizeChunkStrategy
from backend.rag.preprocessing.token_counter import count_tokens


def _ast(text):
    return DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=text),
        ]),
        raw_text=text,
    )


def test_fixed_size_splits_long_text():
    """长文本（超 LEAF_CHUNK_TOKENS）→ 多个 chunk。"""
    text = "长" * (LEAF_CHUNK_TOKENS * 2 + 100)
    chunks = FixedSizeChunkStrategy().split(_ast(text), "x.txt")
    assert len(chunks) > 1


def test_fixed_size_short_text_single_chunk():
    """短文本 → 单 chunk（不切）。"""
    chunks = FixedSizeChunkStrategy().split(_ast("短文本"), "x.txt")
    assert len(chunks) == 1


def test_fixed_size_metadata_complete():
    """chunk metadata 含协议字段。"""
    chunks = FixedSizeChunkStrategy().split(_ast("内容。"), "x.txt")
    for key in ("chunk_id", "granularity", "chunk_tokens", "file_path"):
        assert key in chunks[0].metadata
    assert chunks[0].metadata["granularity"] == "leaf"


def test_ad_policy_routes_to_fixed_size():
    """ad_policy（无结构短文本）路由到 FixedSizeChunkStrategy。"""
    from backend.rag.preprocessing.chunking import ChunkStrategyRouter
    from backend.rag.preprocessing.structure_analyzer import StructureReport

    report = StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=0.9,
    )
    strategy = ChunkStrategyRouter().route("ad_policy", report)
    assert isinstance(strategy, FixedSizeChunkStrategy)
