# backend/tests/rag/test_chunking_router.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, QAChunkStrategy, StructureChunkStrategy,
    RecursiveChunkStrategy,
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
    # Phase 2 修复：FAQ 文档走 QAChunkStrategy（前提：AST 真有 qa 节点）
    faq_report = StructureReport(
        ast=DocumentAST(
            root=DocumentNode(type="section", text="", level=0, children=[
                DocumentNode(type="qa_question", text="怎么退货？"),
                DocumentNode(type="qa_answer", text="提交申请。"),
            ]),
            raw_text="怎么退货？\n提交申请。",
        ),
        completeness=1.0,
    )
    assert isinstance(r.route("faq", faq_report), QAChunkStrategy)


def test_low_completeness_falls_back_to_recursive():
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.3)), RecursiveChunkStrategy)
    assert isinstance(r.route("general", _report(0.1)), RecursiveChunkStrategy)
