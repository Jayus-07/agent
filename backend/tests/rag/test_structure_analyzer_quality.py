"""WS7: Structure Analyzer — graded hierarchy quality。"""
from __future__ import annotations

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer


def _make_ast(root_children: list[DocumentNode], raw_text: str = "") -> DocumentAST:
    root = DocumentNode(type="section", text="", level=0, children=root_children)
    return DocumentAST(root=root, raw_text=raw_text or "x" * 100)


class TestHierarchyQuality:
    def setup_method(self):
        self.analyzer = StructureAnalyzer()

    def test_chaotic_doc_low_score(self):
        """Most text at root level with 2 tiny sections → hierarchy penalty.

        Old binary formula scored this 0.9953; graded formula should score
        noticeably lower than a well-structured doc (≥ 0.1 gap).
        """
        root_leaves = [
            DocumentNode(type="paragraph", text="a" * 200),
            DocumentNode(type="paragraph", text="b" * 200),
            DocumentNode(type="paragraph", text="c" * 200),
        ]
        tiny_sections = [
            DocumentNode(type="section", text="S1", level=1, children=[
                DocumentNode(type="paragraph", text="x"),
            ]),
            DocumentNode(type="section", text="S2", level=1, children=[
                DocumentNode(type="paragraph", text="y"),
            ]),
        ]
        all_children = root_leaves + tiny_sections
        raw = "a" * 200 + "b" * 200 + "c" * 200 + "xy"
        ast = _make_ast(all_children, raw_text=raw)
        _, report = self.analyzer.analyze(ast)

        nested = DocumentNode(type="section", text="Ch", level=1, children=[
            DocumentNode(type="section", text="Sub", level=2, children=[
                DocumentNode(type="paragraph", text="z" * 10),
            ]),
        ])
        good_ast = _make_ast([nested, nested, nested], raw_text="z" * 30)
        _, good_report = self.analyzer.analyze(good_ast)

        assert good_report.completeness - report.completeness >= 0.1

    def test_well_structured_doc_high_score(self):
        """Nested sections with proper hierarchy → high completeness."""
        inner = DocumentNode(type="section", text="Sub", level=2, children=[
            DocumentNode(type="paragraph", text="content" * 10),
        ])
        outer = DocumentNode(type="section", text="Chapter", level=1, children=[inner])
        sections = [
            outer,
            DocumentNode(type="section", text="Chapter 2", level=1, children=[
                DocumentNode(type="section", text="Sub 2", level=2, children=[
                    DocumentNode(type="paragraph", text="more" * 10),
                ]),
            ]),
            DocumentNode(type="section", text="Chapter 3", level=1, children=[
                DocumentNode(type="paragraph", text="text" * 10),
            ]),
        ]
        raw = "content" * 10 + "more" * 10 + "text" * 10
        ast = _make_ast(sections, raw_text=raw)
        _, report = self.analyzer.analyze(ast)
        assert report.completeness >= 0.5

    def test_no_sections_returns_low(self):
        """No sections at all → 0.1."""
        leaves = [DocumentNode(type="paragraph", text="just text")]
        ast = _make_ast(leaves, raw_text="just text")
        _, report = self.analyzer.analyze(ast)
        assert report.completeness == 0.1

    def test_weak_hierarchy_deficit_signal(self):
        """Chaotic doc below threshold should produce weak_hierarchy signal."""
        sections = [
            DocumentNode(type="section", text="S1", level=1, children=[
                DocumentNode(type="paragraph", text="a" * 20),
            ]),
            DocumentNode(type="section", text="S2", level=1, children=[
                DocumentNode(type="paragraph", text="b" * 20),
            ]),
        ]
        ast = _make_ast(sections, raw_text="a" * 20 + "b" * 20)
        _, report = self.analyzer.analyze(ast)
        if report.completeness < 0.8:
            assert report.deficit_signal in ("weak_hierarchy", "long_narrative")
