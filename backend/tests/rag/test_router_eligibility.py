"""WS8: Router-level eligibility checks — legal without clauses → fallback."""
from __future__ import annotations

from unittest.mock import patch

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.structure_analyzer import StructureReport
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter,
    RecursiveChunkStrategy,
    StructureChunkStrategy,
    LegalChunkStrategy,
    QAChunkStrategy,
    _has_legal_clauses,
    _has_qa_nodes,
)


def _make_report(raw_text: str = "x" * 200, children: list | None = None) -> StructureReport:
    root = DocumentNode(type="section", text="", level=0, children=children or [])
    ast = DocumentAST(root=root, raw_text=raw_text)
    return StructureReport(ast=ast, completeness=0.9)


class TestHasLegalClauses:
    def test_with_clause(self):
        root = DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="第一条", children=[]),
        ])
        ast = DocumentAST(root=root, raw_text="第一条")
        assert _has_legal_clauses(ast)

    def test_without_clause(self):
        root = DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="普通文本", children=[]),
        ])
        ast = DocumentAST(root=root, raw_text="普通文本")
        assert not _has_legal_clauses(ast)


class TestHasQaNodes:
    def test_with_qa(self):
        root = DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="Q?", children=[]),
            DocumentNode(type="qa_answer", text="A.", children=[]),
        ])
        ast = DocumentAST(root=root, raw_text="Q? A.")
        assert _has_qa_nodes(ast)

    def test_without_qa(self):
        root = DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="text", children=[]),
        ])
        ast = DocumentAST(root=root, raw_text="text")
        assert not _has_qa_nodes(ast)


class TestRouterEligibility:
    def setup_method(self):
        self.router = ChunkStrategyRouter()

    def test_legal_with_clauses(self):
        """Legal doc with actual clauses → LegalChunkStrategy."""
        children = [DocumentNode(type="paragraph", text="第一条")]
        report = _make_report(children=children)
        strategy = self.router.route("legal", report)
        assert isinstance(strategy, LegalChunkStrategy)

    def test_legal_without_clauses_with_sections(self):
        """Legal doc without clauses but with sections → StructureChunkStrategy."""
        children = [
            DocumentNode(type="section", text="Section", level=1, children=[
                DocumentNode(type="paragraph", text="采购流程说明"),
            ]),
        ]
        report = _make_report(children=children)
        strategy = self.router.route("legal", report)
        assert isinstance(strategy, StructureChunkStrategy)

    def test_legal_without_clauses_no_sections(self):
        """Legal doc without clauses or sections → RecursiveChunkStrategy."""
        children = [DocumentNode(type="paragraph", text="普通文本")]
        report = _make_report(children=children)
        strategy = self.router.route("legal", report)
        assert isinstance(strategy, RecursiveChunkStrategy)

    def test_contract_template_without_clauses(self):
        """contract_template without clauses → fallback."""
        children = [
            DocumentNode(type="section", text="Sec", level=1, children=[
                DocumentNode(type="paragraph", text="内容"),
            ]),
        ]
        report = _make_report(children=children)
        strategy = self.router.route("contract_template", report)
        assert isinstance(strategy, StructureChunkStrategy)

    def test_faq_without_qa_nodes(self):
        """FAQ doc_type without qa nodes → RecursiveChunkStrategy."""
        children = [DocumentNode(type="paragraph", text="FAQ 内容但没有 Q/A 结构")]
        report = _make_report(children=children)
        strategy = self.router.route("faq", report)
        assert isinstance(strategy, RecursiveChunkStrategy)

    def test_faq_with_qa_nodes(self):
        """FAQ doc_type with qa nodes → QAChunkStrategy."""
        children = [
            DocumentNode(type="qa_question", text="Q?"),
            DocumentNode(type="qa_answer", text="A."),
        ]
        report = _make_report(children=children)
        strategy = self.router.route("faq", report)
        assert isinstance(strategy, QAChunkStrategy)
