"""企业 RAG 解析、语义边界与质量门禁契约。"""
from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    LegalChunkStrategy,
    coverage_audit,
)
from backend.rag.preprocessing.quality_gate import (
    audit_legal_chunk_boundaries,
    build_quality_record,
    hard_anomalies,
    run_typed_validation,
)
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer


def _merged_policy_ast() -> DocumentAST:
    text = (
        "第一条 适用范围\n本规定适用于全体教职工。\n"
        "第二条 管理原则\n学校依规履行管理职责。\n"
        "第三条 施行日期\n本规定自发布之日起施行。"
    )
    return DocumentAST(
        root=DocumentNode(
            type="section",
            text="",
            level=0,
            children=[DocumentNode(
                type="paragraph",
                text=text,
                page_number=4,
                bbox=(10.0, 20.0, 300.0, 700.0),
            )],
        ),
        source_file="policy.pdf",
        raw_text=text,
    )


def test_structure_analyzer_splits_inline_policy_clauses_without_mutating_raw_ast():
    raw_ast = _merged_policy_ast()
    original_text = raw_ast.root.children[0].text

    normalized, report = StructureAnalyzer().analyze(raw_ast)

    assert normalized is not raw_ast
    assert raw_ast.root.children[0].text == original_text
    assert report.profile.legal_clause_count == 3
    clauses = normalized.root.children
    assert len(clauses) == 3
    assert [node.text.splitlines()[0] for node in clauses] == [
        "第一条 适用范围",
        "第二条 管理原则",
        "第三条 施行日期",
    ]
    assert all(node.page_number == 4 for node in clauses)
    assert all(node.bbox == (10.0, 20.0, 300.0, 700.0) for node in clauses)


def test_legal_chunks_keep_one_article_boundary_and_complete_coverage():
    raw_ast = _merged_policy_ast()
    normalized, _ = StructureAnalyzer().analyze(raw_ast)

    chunks = LegalChunkStrategy().split(normalized, "policy.pdf")
    leaves = [chunk for chunk in chunks if chunk.metadata["granularity"] == "leaf"]

    assert len(leaves) == 3
    assert [chunk.metadata["section_title"] for chunk in leaves] == [
        "第一条", "第二条", "第三条",
    ]
    assert coverage_audit(normalized, chunks)["uncovered_count"] == 0
    assert audit_legal_chunk_boundaries(
        raw_ast, chunks, "LegalChunkStrategy",
    )["passed"] is True


def test_legal_boundary_gate_rejects_two_articles_in_one_leaf():
    raw_ast = _merged_policy_ast()
    combined = "\n".join(
        [raw_ast.root.children[0].text]
    )
    bad_chunk = Document(
        page_content=combined,
        metadata={
            "chunk_id": "combined-policy-articles",
            "granularity": "leaf",
            "parent_chunk_id": "",
            "section_title": "第一条",
        },
    )

    record = build_quality_record(
        file_path="policy.pdf",
        doc_id="policy-doc",
        kb_id="policy-kb",
        raw_ast=raw_ast,
        chunks=[bad_chunk],
        file_size=100,
        file_hash="abc",
        strategy_name="LegalChunkStrategy",
    )
    anomalies = run_typed_validation(record, raw_ast)

    assert record["semantic_boundaries"]["passed"] is False
    hard_checks = {item["check"] for item in hard_anomalies(anomalies)}
    assert "legal_clause_boundaries" in hard_checks


def test_parser_issues_are_retained_in_quality_record_and_validation():
    ast = DocumentAST(
        root=DocumentNode(
            type="section", text="", level=0,
            children=[DocumentNode(type="paragraph", text="足够长的政策正文，用于解析质量验证。")],
        ),
        raw_text="足够长的政策正文，用于解析质量验证。",
        parse_status="partial",
        parse_issues=[{
            "code": "pdf_visual_content_unparsed",
            "severity": "warn",
            "page_numbers": [2],
            "detail": "页面含大面积图片但没有图像语义。",
        }],
        ocr_required_regions=[{"page": 2, "bbox": [10.0, 20.0, 300.0, 700.0]}],
        ocr_failed_regions=[{"page": 2, "bbox": [10.0, 20.0, 300.0, 700.0]}],
        pdf_image_count=1,
        pdf_image_pages=[2],
        pdf_unparsed_visual_pages=[2],
    )
    chunk = Document(
        page_content="足够长的政策正文，用于解析质量验证。",
        metadata={"chunk_id": "policy-leaf", "granularity": "leaf"},
    )

    record = build_quality_record(
        file_path="policy.pdf",
        doc_id="policy-doc",
        kb_id="policy-kb",
        raw_ast=ast,
        chunks=[chunk],
        file_size=100,
        file_hash="abc",
    )
    anomalies = run_typed_validation(record, ast)

    assert record["schema_version"] == "1.3"
    assert record["parsing"]["parse_status"] == "partial"
    assert record["parsing"]["pdf_image_pages"] == [2]
    assert record["parsing"]["ocr_required_regions"] == [
        {"page": 2, "bbox": [10.0, 20.0, 300.0, 700.0]},
    ]
    assert record["parsing"]["ocr_failed_regions"] == [
        {"page": 2, "bbox": [10.0, 20.0, 300.0, 700.0]},
    ]
    issue = next(item for item in anomalies if item["check"] == "parser_pdf_visual_content_unparsed")
    assert issue["severity"] == "warn"
    assert "[2]" in issue["detail"]


def test_required_but_unavailable_ocr_is_a_hard_quality_failure():
    ast = DocumentAST(
        root=DocumentNode(
            type="section", text="", level=0,
            children=[DocumentNode(type="paragraph", text="已有文本层内容，但扫描页仍需 OCR。")],
        ),
        raw_text="已有文本层内容，但扫描页仍需 OCR。",
        ocr_required=True,
        ocr_attempted=False,
        ocr_required_pages=[2],
        parse_status="partial",
    )
    chunk = Document(
        page_content="已有文本层内容，但扫描页仍需 OCR。",
        metadata={"chunk_id": "policy-leaf", "granularity": "leaf"},
    )

    record = build_quality_record(
        file_path="mixed.pdf",
        doc_id="mixed-doc",
        kb_id="policy-kb",
        raw_ast=ast,
        chunks=[chunk],
        file_size=100,
        file_hash="abc",
    )
    hard_checks = {
        item["check"]
        for item in hard_anomalies(run_typed_validation(record, ast))
    }

    assert record["parsing"]["ocr_required_pages"] == [2]
    assert "ocr_coverage_partial" in hard_checks
