"""test_semantic_chunking.py — SemanticChunkStrategy 语义切分。

覆盖：
1. 句子切分 / 余弦相似度 / 语义边界检测（纯函数）
2. SemanticChunkStrategy 按 embedding 相似度在语义断层处切分
3. Router 对无结构长文档路由到 SemanticChunkStrategy
"""
import pytest

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, SemanticChunkStrategy,
    _cosine_similarity, _detect_boundaries, _split_sentences,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


# ─── 纯函数 ───

def test_split_sentences():
    text = "第一句。第二句！第三句？\n第四句；第五句"
    assert _split_sentences(text) == ["第一句", "第二句", "第三句", "第四句", "第五句"]


def test_split_sentences_filters_empty():
    text = "第一句。。\n\n第二句"
    assert _split_sentences(text) == ["第一句", "第二句"]


def test_cosine_similarity():
    assert _cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    # 零向量不抛异常，返回 0
    assert _cosine_similarity([0, 0], [1, 1]) == pytest.approx(0.0)


def test_detect_boundaries():
    """相似度骤降处标记为边界（新 chunk 起点）。"""
    vecs = [[1, 0], [0.9, 0.1], [0.1, 0.9], [0, 1]]
    starts = _detect_boundaries(["a", "b", "c", "d"], vecs, threshold=0.5)
    # a-b 相似(无边界)，b-c 不相似(边界)，c-d 相似(无边界)
    assert starts == [True, False, True, False]


# ─── SemanticChunkStrategy ───

class _FakeEmbedding:
    """按文本映射返回固定向量。"""

    def __init__(self, mapping: dict):
        self.mapping = mapping

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.mapping[t] for t in texts]


def test_semantic_split_by_topic_boundary():
    """主题 A 的两句和主题 B 的两句，应在语义断层处切成 2 个 chunk。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="主题A第一句。主题A第二句。主题B第一句。主题B第二句。"),
        ]),
        raw_text="主题A第一句。主题A第二句。主题B第一句。主题B第二句。",
    )
    embedding = _FakeEmbedding({
        "主题A第一句": [1.0, 0.0],
        "主题A第二句": [0.9, 0.1],   # 与 A1 相似
        "主题B第一句": [0.1, 0.9],   # 与 A2 不相似 → 边界
        "主题B第二句": [0.0, 1.0],   # 与 B1 相似
    })
    chunks = SemanticChunkStrategy()._split_with_embedding(ast, "x.md", embedding)
    assert len(chunks) == 2
    assert "主题A第一句" in chunks[0].page_content
    assert "主题A第二句" in chunks[0].page_content
    assert "主题B第一句" in chunks[1].page_content
    assert "主题B第二句" in chunks[1].page_content


def test_semantic_chunk_metadata_complete():
    """chunk metadata 必须含 chunk_id / granularity / chunk_tokens 等协议字段。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="一句话。"),
        ]),
        raw_text="一句话。",
    )
    embedding = _FakeEmbedding({"一句话": [1.0, 0.0]})
    chunks = SemanticChunkStrategy()._split_with_embedding(ast, "x.md", embedding)
    assert len(chunks) == 1
    for key in ("chunk_id", "granularity", "chunk_tokens", "source_file", "file_path"):
        assert key in chunks[0].metadata
    assert chunks[0].metadata["granularity"] == "leaf"


# ─── Router 路由 ───

def _unstructured_long_report():
    """无结构长文档的 report（completeness 低 + raw_text 长）。"""
    return StructureReport(
        ast=DocumentAST(
            root=DocumentNode(type="section", text="", level=0, children=[
                DocumentNode(type="paragraph", text="长" * 1000),
            ]),
            raw_text="长" * 1000,
        ),
        completeness=0.1,
    )


def test_router_semantic_enabled_routes_semantic(monkeypatch):
    """ENABLE_SEMANTIC_CHUNKING=true 且无结构长文档 → SemanticChunkStrategy。"""
    import backend.config as config
    monkeypatch.setattr(config, "ENABLE_SEMANTIC_CHUNKING", True)
    strategy = ChunkStrategyRouter().route("general", _unstructured_long_report())
    assert isinstance(strategy, SemanticChunkStrategy)


def test_router_semantic_disabled_falls_back_recursive(monkeypatch):
    """开关关闭时，无结构长文档仍走 Recursive（不回归）。"""
    import backend.config as config
    monkeypatch.setattr(config, "ENABLE_SEMANTIC_CHUNKING", False)
    from backend.rag.preprocessing.chunking import RecursiveChunkStrategy
    strategy = ChunkStrategyRouter().route("general", _unstructured_long_report())
    assert isinstance(strategy, RecursiveChunkStrategy)
