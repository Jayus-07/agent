"""企业文档解析与结构来源元数据的定向验收。"""
from __future__ import annotations

import json
import zipfile

import pytest

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, annotate_ast_provenance, walk
from backend.rag.preprocessing.parser import parse_file
from backend.rag.preprocessing.pipeline import parse_and_chunk_full
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter,
    ContentUnitDispatchStrategy,
    QAChunkStrategy,
    StructureChunkStrategy,
    _local_strategy_class,
    _recursive_content_units,
)


def _write_pptx(path, slide_xml: str, core_xml: str = "") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide_xml)
        if core_xml:
            archive.writestr("docProps/core.xml", core_xml)


def test_new_parsers_keep_structure_and_native_locators(tmp_path):
    html_path = tmp_path / "handbook.html"
    html_path.write_text(
        "<html><head><title>員工手冊</title></head><body><nav>導覽</nav>"
        "<div class=\"ad-banner\">廣告位</div>"
        "<h1><span>考勤</span>制度</h1><p>工作時間為九點至六點。</p>"
        "<table><tr><th>項目</th><th>天數</th></tr>"
        "<tr><td>年假</td><td>5</td></tr></table></body></html>",
        encoding="utf-8",
    )
    html_ast = parse_file(str(html_path))
    html_nodes = list(walk(html_ast.root))
    assert html_ast.document_title == "員工手冊"
    assert not any("導覽" in node.text for node in html_nodes)
    assert not any("廣告位" in node.text for node in html_nodes)
    assert any(node.text == "考勤制度" and node.type == "section" for node in html_nodes)
    html_table = next(node for node in html_nodes if node.type == "table")
    assert html_table.rows == [["項目", "天數"], ["年假", "5"]]
    assert html_table.source_locator["dom_path"]
    assert all(node.node_id for node in html_nodes)

    json_path = tmp_path / "records.json"
    json_path.write_text(
        json.dumps([{"name": "工單 A", "status": "open"}, {"name": "工單 B", "status": "closed"}]),
        encoding="utf-8",
    )
    json_ast = parse_file(str(json_path))
    record_sections = [
        node for node in walk(json_ast.root)
        if node.type == "section" and node.source_locator.get("json_path", "").startswith("$[")
    ]
    assert len(record_sections) == 2
    assert all(node.source_locator.get("record_id") for node in record_sections)
    assert any(".status" in node.source_locator.get("json_path", "") for node in walk(json_ast.root))

    xml_path = tmp_path / "records.xml"
    xml_path.write_text(
        "<orders><item>one</item><item>two</item></orders>", encoding="utf-8",
    )
    xml_ast = parse_file(str(xml_path))
    xml_table = next(node for node in walk(xml_ast.root) if node.type == "table")
    assert xml_table.rows == [["value"], ["one"], ["two"]]
    assert xml_table.source_locator["xpath"]


def test_pptx_parser_extracts_title_text_and_table(tmp_path):
    path = tmp_path / "guide.pptx"
    slide_xml = """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree>
        <p:nvGrpSpPr/><p:grpSpPr/>
        <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/>
          <p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
          <p:txBody><a:p><a:r><a:t>操作指南</a:t></a:r></a:p></p:txBody></p:sp>
        <p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
          <p:txBody><a:p><a:r><a:t>第一步：登录系统</a:t></a:r></a:p></p:txBody></p:sp>
        <p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table"/>
          <p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm/>
          <a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>功能</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>入口</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
            <a:tr><a:tc><a:txBody><a:p><a:r><a:t>报表</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>数据中心</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
          </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
      </p:spTree></p:cSld></p:sld>"""
    core_xml = """<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
      xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>系统说明</dc:title></cp:coreProperties>"""
    _write_pptx(path, slide_xml, core_xml)

    ast = parse_file(str(path))
    nodes = list(walk(ast.root))
    assert ast.document_title == "系统说明"
    assert nodes[1].text == "操作指南"
    assert any(node.text == "第一步：登录系统" for node in nodes)
    table = next(node for node in nodes if node.type == "table")
    assert table.rows == [["功能", "入口"], ["报表", "数据中心"]]
    assert table.source_locator["slide"] == 1
    assert ast.source_format == "pptx"


def test_stable_ids_and_recursive_mixed_structure_routing():
    ast = DocumentAST(
        root=DocumentNode(
            type="section", text="", level=0, children=[
                DocumentNode(type="section", text="售后", level=1, children=[
                    DocumentNode(type="paragraph", text="服务时间为工作日。"),
                    DocumentNode(type="section", text="常见问题", level=2, children=[
                        DocumentNode(type="qa_question", text="退款何时到账？"),
                        DocumentNode(type="qa_answer", text="审核通过后 1–3 个工作日。"),
                    ]),
                ]),
            ],
        ),
        raw_text="售后\n服务时间为工作日。\n常见问题\n退款何时到账？\n审核通过后 1–3 个工作日。",
        source_file="guide.txt",
    )
    annotate_ast_provenance(ast, "v1")
    first_ids = [node.node_id for node in walk(ast.root)]
    annotate_ast_provenance(ast, "v1")
    assert [node.node_id for node in walk(ast.root)] == first_ids

    units = _recursive_content_units(ast)
    assert len(units) == 2
    assert units[0][1] == []
    assert units[1][1] == ["售后"]
    assert units[0][0].root.children[0].text == "售后"
    assert units[1][0].root.children[0].text == "常见问题"
    assert _local_strategy_class(units[0][0], "general") is StructureChunkStrategy
    assert _local_strategy_class(units[1][0], "general") is QAChunkStrategy

    from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer

    report = StructureAnalyzer().analyze(ast)[1]
    strategy, decision = ChunkStrategyRouter().route_with_decision("general", report)
    assert isinstance(strategy, ContentUnitDispatchStrategy)
    assert decision.strategy_version == "v2"
    chunks = strategy.split(report.ast, "guide.txt")
    faq_chunk = next(chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "faq")
    assert faq_chunk.metadata["section_path"] == ["售后", "常见问题"]
    assert faq_chunk.metadata["question"] == "退款何时到账？"
    assert faq_chunk.metadata["faq_id"]


def test_section_tree_includes_explicit_document_title_and_page_span():
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(
                type="section", text="费用条款", level=1, page_number=1,
                children=[DocumentNode(
                    type="paragraph", text="费用按月结算。", page_number=2,
                )],
            ),
        ]),
        raw_text="费用条款\n费用按月结算。",
        document_title="技术服务合同",
        source_file="contract.pdf",
    )
    section = annotate_ast_provenance(ast, "v1")[0]
    assert section["heading_path"] == ["技术服务合同", "费用条款"]
    assert (section["page_start"], section["page_end"]) == (1, 2)


def test_rewritten_table_row_keeps_table_header_and_row_locator():
    from langchain_core.documents import Document

    from backend.rag.preprocessing.chunking import stamp_chunk_structure

    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="服务费用", level=1, children=[
                DocumentNode(
                    type="table",
                    text="项目：应急响应；单价：3530；数量：8。",
                    rows=[["项目", "单价", "数量"], ["应急响应", "3530", "8"]],
                    source_locator={"worksheet": "Sheet1", "row_start": 1, "row_end": 1},
                ),
            ]),
        ]),
        raw_text="服务费用\n项目：应急响应；单价：3530；数量：8。",
        source_file="contract.xlsx",
    )
    annotate_ast_provenance(ast, "v1")
    chunk = Document(
        page_content="项目：应急响应；数量：8。",
        metadata={
            "section_path": ["服务费用"],
            "chunk_type": "table_row",
            "table_id": "table-1",
            "row_index": 1,
        },
    )
    stamp_chunk_structure([chunk], ast)
    locator = json.loads(chunk.metadata["source_locators_json"])[0]
    assert locator["worksheet"] == "Sheet1"
    assert locator["columns"] == ["项目", "单价", "数量"]
    assert locator["row_index"] == 1
    assert locator["table_id"] == "table-1"


def test_pipeline_emits_section_and_index_policy_metadata(tmp_path):
    from backend.rag.preprocessing.pipeline import parse_and_chunk_full

    path = tmp_path / "policy.html"
    path.write_text(
        "<h1>退货政策</h1><p>商品签收后七日内可申请退货，需保持商品完整。</p>",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(path), "general")
    leaves = [chunk for chunk in chunks if chunk.metadata.get("granularity") == "leaf"]
    assert leaves
    assert qc["section_tree"]
    assert leaves[0].metadata["section_id"]
    assert leaves[0].metadata["parser_version"] == "v2"
    assert leaves[0].metadata["chunk_policy_version"].startswith("StructureChunkStrategy:")
    assert leaves[0].metadata["source_locators_json"]
    assert json.loads(leaves[0].metadata["heading_path_json"]) == ["退货政策"]


def test_content_embedding_and_search_text_remain_distinct():
    from langchain_core.documents import Document

    from backend.rag.indexing.stages.embedding_stage import EmbeddingStage

    chunk = Document(
        page_content="SKU-10086 有效期为一年。",
        metadata={"section_title": "服务条款"},
    )
    assert EmbeddingStage.content_for(chunk) == "SKU-10086 有效期为一年。"
    assert EmbeddingStage.search_text_for(chunk) == chunk.page_content
    assert "【章节】服务条款" in EmbeddingStage.embedding_text_for(chunk)

    from backend.rag.preprocessing.entity import extract_exact_terms

    terms = extract_exact_terms("ThinkPad T14 使用 SKU-10086，故障码 ERR_CONN_001。")
    assert "ThinkPad T14" in terms
    assert "SKU-10086" in terms
    assert "ERR_CONN_001" in terms


def test_ocr_quality_flags_distinguish_low_and_unavailable_scores():
    from types import SimpleNamespace

    from backend.rag.preprocessing.pipeline import _ocr_quality_flags

    low = SimpleNamespace(
        ocr_triggered=True,
        ocr_confidence_min=0.62,
        ocr_confidence_scored_regions=2,
        ocr_confidence_unavailable_regions=0,
    )
    assert _ocr_quality_flags(low) == ["ocr_fallback", "ocr_confidence_low"]

    unavailable = SimpleNamespace(
        ocr_triggered=True,
        ocr_confidence_min=None,
        ocr_confidence_scored_regions=0,
        ocr_confidence_unavailable_regions=1,
    )
    assert _ocr_quality_flags(unavailable) == [
        "ocr_fallback", "ocr_confidence_unavailable",
    ]


def test_xml_rejects_entity_declarations(tmp_path):
    path = tmp_path / "unsafe.xml"
    path.write_text("<!DOCTYPE x [<!ENTITY a 'bad'>]><x>&a;</x>", encoding="utf-8")
    from backend.rag.preprocessing.parser.xml_parser import XmlParser

    with pytest.raises(ValueError, match="DTD/ENTITY"):
        XmlParser().parse(str(path))


def test_json_and_xml_structure_limits_mark_partial_parse(tmp_path, monkeypatch):
    from backend.rag.preprocessing.parser import json_parser, xml_parser

    json_path = tmp_path / "wide.json"
    json_path.write_text('{"a":1,"b":2,"c":3}', encoding="utf-8")
    monkeypatch.setattr(json_parser, "MAX_JSON_NODES", 2)
    json_ast = json_parser.JsonParser().parse(str(json_path))
    assert json_ast.parse_status == "partial"
    assert json_ast.parse_issues[0]["code"] == "json_structure_limit"

    xml_path = tmp_path / "wide.xml"
    xml_path.write_text('<root a="1" b="2" c="3"/>', encoding="utf-8")
    monkeypatch.setattr(xml_parser, "_MAX_XML_NODES", 2)
    xml_ast = xml_parser.XmlParser().parse(str(xml_path))
    assert xml_ast.parse_status == "partial"
    assert xml_ast.parse_issues[0]["code"] == "xml_structure_limit"


def test_markdown_parses_fenced_code_and_pipe_table(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text(
        "# 手册\n\n## 安装\n\n执行安装。\n\n"
        "```bash\npip install demo\n```\n\n"
        "| 参数 | 说明 |\n|---|---|\n| a | 甲 |\n",
        encoding="utf-8",
    )
    ast = parse_file(str(path))
    nodes = list(walk(ast.root))
    code = next(node for node in nodes if node.type == "code")
    assert code.text == "pip install demo"
    table = next(node for node in nodes if node.type == "table")
    assert table.rows == [["参数", "说明"], ["a", "甲"]]
    assert table.source_locator["line_start"] == 11

    chunks, qc = parse_and_chunk_full(str(path), "general")
    assert any(chunk.metadata.get("chunk_type") == "code" for chunk in chunks)
    assert any(
        str(chunk.metadata.get("chunk_type") or "").startswith("table_")
        for chunk in chunks
    )
    assert qc["section_tree"]
    assert qc["coverage"]["uncovered_count"] == 0


def test_pptx_untitled_slides_keep_one_section_per_slide(tmp_path):
    def _slide(text: str) -> str:
        return (
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Body\"/>"
            "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r><a:t>"
            + text
            + "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )

    path = tmp_path / "untitled.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", _slide("第一页内容"))
        archive.writestr("ppt/slides/slide2.xml", _slide("第二页内容"))

    ast = parse_file(str(path))
    sections = [
        node.text for node in walk(ast.root) if node.type == "section"
    ]
    assert sections[1:] == ["幻灯片 1", "幻灯片 2"]

    chunks, qc = parse_and_chunk_full(str(path), "general")
    assert {chunk.metadata.get("section_type") for chunk in chunks} == {"slide"}
    assert all(chunk.metadata.get("section_path") for chunk in chunks)
    assert not any(
        "第一页内容" in chunk.page_content and "第二页内容" in chunk.page_content
        for chunk in chunks
    )
    assert [item["section_type"] for item in qc["section_tree"]] == [
        "slide", "slide",
    ]


def test_same_section_multiple_tables_keep_distinct_locators(tmp_path):
    path = tmp_path / "tables.html"
    path.write_text(
        "<h1>费用</h1>"
        "<table><tr><th>项目</th><th>金额</th></tr>"
        "<tr><td>A</td><td>1</td></tr></table>"
        "<table><tr><th>人员</th><th>天数</th></tr>"
        "<tr><td>B</td><td>2</td></tr></table>",
        encoding="utf-8",
    )
    chunks, _qc = parse_and_chunk_full(str(path), "legal")
    rows = [
        chunk for chunk in chunks
        if chunk.metadata.get("chunk_type") == "table_row"
    ]
    assert len(rows) == 2
    locators = [
        json.loads(chunk.metadata["source_locators_json"])[0]
        for chunk in rows
    ]
    assert locators[0]["dom_path"] != locators[1]["dom_path"]
    assert locators[0]["columns"] == ["项目", "金额"]
    assert locators[1]["columns"] == ["人员", "天数"]
    assert all(chunk.metadata.get("table_node_id") for chunk in rows)


def test_mixed_faq_and_contract_do_not_duplicate_faq_or_lose_clause_ids(tmp_path):
    path = tmp_path / "mixed.md"
    path.write_text(
        "# 技术服务合同\n\n"
        "第一条 总则\n本合同适用。\n"
        "第二条 费用\n按月结算。\n"
        "第三条 期限\n一年。\n\n"
        "# 售后 FAQ\n\n"
        "Q：商品多久可以退货？\nA：签收后 7 天内。\n\n"
        "Q：退款何时到账？\nA：1-3 个工作日。\n",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(path), "general")
    faq = [
        chunk for chunk in chunks
        if chunk.metadata.get("chunk_type") == "faq"
    ]
    assert len(faq) == 2
    assert len({chunk.page_content for chunk in faq}) == 2
    assert all(chunk.metadata.get("faq_id") for chunk in faq)

    legal_leaves = [
        chunk for chunk in chunks
        if chunk.metadata.get("chunk_type") == "legal_clause"
    ]
    assert legal_leaves
    assert all(chunk.metadata.get("section_id") for chunk in legal_leaves)
    assert all(
        chunk.metadata.get("section_type") == "clause"
        for chunk in legal_leaves
    )
    clause_sections = [
        item for item in qc["section_tree"]
        if item.get("section_type") == "clause"
    ]
    assert len(clause_sections) == 3


def test_quality_record_uses_explicit_index_metadata_title_and_language():
    from langchain_core.documents import Document

    from backend.rag.preprocessing.quality_gate import build_quality_record

    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="正文内容足够长。" * 10),
        ]),
        raw_text="正文内容足够长。" * 10,
        source_file="demo.md",
    )
    chunks = [Document(
        page_content="正文内容足够长。" * 10,
        metadata={
            "granularity": "leaf",
            "chunk_id": "c1",
            "source_spans_json": json.dumps([{"start": 0, "end": 7}]),
        },
    )]
    record = build_quality_record(
        file_path="demo.md", doc_id="d", kb_id="kb",
        raw_ast=ast, chunks=chunks, file_size=1, file_hash="x",
        index_metadata={
            "chunk_policy_version": "S:v1",
            "embedding_model": "m1",
            "embedding_version": "r1",
            "index_generation": "g1",
            "indexed_at": "2026-10-22T00:00:00+00:00",
        },
        document_title="演示文档",
        language="zh",
    )
    assert record["title"] == "演示文档"
    assert record["language"] == "zh"
    assert record["index_metadata"] == {
        "chunk_policy_version": "S:v1",
        "embedding_model": "m1",
        "embedding_version": "r1",
        "index_generation": "g1",
        "indexed_at": "2026-10-22T00:00:00+00:00",
    }
    assert record["traceability"]["citation_locator"][
        "missing_locator_count"
    ] == 0


def test_document_language_detection_is_deterministic():
    from backend.rag.preprocessing.metadata import detect_document_language

    assert detect_document_language("员工手册与考勤制度") == "zh"
    assert detect_document_language("Employee handbook and attendance") == "en"
    assert detect_document_language("2026-10-22") == ""

