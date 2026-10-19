# backend/tests/rag/test_chunking_router.py
# v3 结构驱动契约（P0-1 治理改造，2026-10-19）：无结构文档不再由
# doc_type 映射救援；有 qa 节点的文档无论分类如何都走 QA。
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, QAChunkStrategy, RecursiveChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


def _report(completeness: float):
    return StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=completeness,
    )


def test_no_structure_routes_to_recursive_regardless_of_doc_type():
    """v3：空结构文档递归兜底，doc_type（policy/general）不再救援。"""
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.9)), RecursiveChunkStrategy)
    assert isinstance(r.route("general", _report(0.1)), RecursiveChunkStrategy)


def test_qa_nodes_route_to_qa_strategy():
    """有 qa 节点 → QA 切分（结构信号直判，doc_type 无关）。"""
    r = ChunkStrategyRouter()
    faq_report = StructureReport(
        ast=DocumentAST(
            root=DocumentNode(type="section", text="", level=0, children=[
                DocumentNode(type="qa_question", text="怎么退货？"),
                DocumentNode(type="qa_answer", text="提交申请。"),
            ]),
            raw_text="怎么退货？ 提交申请。",
        ),
        completeness=1.0,
    )
    assert isinstance(r.route("faq", faq_report), QAChunkStrategy)
