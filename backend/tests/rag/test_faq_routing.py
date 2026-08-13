"""test_faq_routing.py — faq 路由修复验证。

覆盖：
1. faq + completeness=0.9 → QAChunkStrategy
2. 其他 doc_type 不受影响（回归）
3. QAChunkStrategy 切 qa_* 节点产 leaf
4. QA leaf 含 file_path / chunk_id metadata（与 Phase 1 协议一致）
"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, QAChunkStrategy, StepChunkStrategy,
    StructureChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


def _report(completeness: float):
    return StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=completeness,
    )


def test_faq_routes_to_qa_strategy():
    """Phase 2 修复：faq 走 QAChunkStrategy（不再走 StructureChunkStrategy）。"""
    r = ChunkStrategyRouter()
    strategy = r.route("faq", _report(0.9))
    assert isinstance(strategy, QAChunkStrategy)


def test_other_types_unchanged():
    """回归验证：其他 doc_type 路由不变。

    - policy → StructureChunkStrategy
    - sop → StepChunkStrategy（Phase 1 占位，与 Structure 同 split 行为）
    """
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.9)), StructureChunkStrategy)
    assert isinstance(r.route("sop", _report(0.9)), StepChunkStrategy)


def test_qa_strategy_handles_qa_nodes():
    """QAChunkStrategy 切 qa_question/qa_answer 节点产 leaf。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
        ]),
    )
    chunks = QAChunkStrategy().split(ast, "faq.md")
    assert len(chunks) == 2
    assert all(c.metadata["granularity"] == "leaf" for c in chunks)


def test_qa_strategy_chunks_have_required_metadata():
    """QA leaf 必须有 chunk_id + file_path（与 Phase 1 协议一致）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
        ]),
    )
    chunks = QAChunkStrategy().split(ast, "/abs/path/faq.md")
    assert all("chunk_id" in c.metadata for c in chunks)
    assert all("file_path" in c.metadata for c in chunks)
    assert chunks[0].metadata["file_path"] == "/abs/path/faq.md"
