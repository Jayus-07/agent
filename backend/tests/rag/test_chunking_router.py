# backend/tests/rag/test_chunking_router.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, StructureChunkStrategy, RecursiveChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


def _report(completeness: float):
    return StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=completeness,
    )


def test_structure_complete_routes_by_doc_type():
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.9)), StructureChunkStrategy)
    # FAQ 文档走 Structure 策略（QAChunkStrategy 留 Phase 2，Phase 1 无 parser 产出 qa_* 节点）
    assert isinstance(r.route("faq", _report(0.9)), StructureChunkStrategy)


def test_low_completeness_falls_back_to_recursive():
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.3)), RecursiveChunkStrategy)
    assert isinstance(r.route("general", _report(0.1)), RecursiveChunkStrategy)
