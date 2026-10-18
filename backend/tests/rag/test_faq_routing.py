"""test_faq_routing.py — faq 路由修复验证。

覆盖：
1. faq + completeness=0.9 → QAChunkStrategy
2. 其他 doc_type 不受影响（回归）
3. QAChunkStrategy 切 qa_* 节点产 leaf
4. QA leaf 含 file_path / chunk_id metadata（与 Phase 1 协议一致）
"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, QAChunkStrategy, RecursiveChunkStrategy,
    StepChunkStrategy, StructureChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


def _report(completeness: float):
    return StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=completeness,
    )


def test_faq_routes_to_qa_strategy():
    """faq + AST 有 qa 节点 → QAChunkStrategy（缺陷 A 修复的核心断言）。

    前置：AST 必须真有 qa_* 节点（真实 FAQ 经 parser 产出），
    用空 AST 的 report 无法命中 QAChunkStrategy（见缺陷 D fallback）。
    """
    from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer

    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
        ]),
        raw_text="怎么退货？\n提交申请。",
    )
    _, report = StructureAnalyzer().analyze(ast)
    strategy = ChunkStrategyRouter().route("faq", report)
    assert isinstance(strategy, QAChunkStrategy)


def test_other_types_unchanged():
    """回归验证（v3 结构驱动更新，2026-10-19）：

    - 有章节结构的文档 → StructureChunkStrategy（doc_type 无关）
    - ≥3 个中文序号标题 → StepChunkStrategy（结构信号直判）
    """
    from backend.rag.preprocessing.ast import DocumentAST as _AST
    from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer as _SA

    def _structured_report():
        ast = _AST(root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="第一章 总则", level=1,
                         children=[DocumentNode(type="paragraph", text="正文")]),
        ]))
        _, rep = _SA().analyze(ast)
        return rep

    def _step_report():
        children = [
            DocumentNode(type="section", text=f"{cn}、步骤{i}", level=1,
                         children=[DocumentNode(type="paragraph", text=f"内容{i}")])
            for i, cn in enumerate(["一", "二", "三"], start=1)
        ]
        ast = _AST(root=DocumentNode(type="section", text="", level=0, children=children))
        _, rep = _SA().analyze(ast)
        return rep

    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _structured_report()), StructureChunkStrategy)
    assert isinstance(r.route("sop", _step_report()), StepChunkStrategy)
    # 无结构文档递归兜底（doc_type 不再救援）
    assert isinstance(r.route("policy", _report(0.9)), RecursiveChunkStrategy)


def test_qa_strategy_handles_qa_nodes():
    """QAChunkStrategy 将 qa_question/qa_answer 对合并为一个 chunk（P1-6）。

    原实现 Q/A 各自独立成 chunk（问答分离）；修复后 Q+A 合并为
    "Q：...\nA：..." 单一 chunk，保证 FAQ 条目语义完整。
    """
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
        ]),
    )
    chunks = QAChunkStrategy().split(ast, "faq.md")
    # C1 起：1 leaf + 1 文档级虚拟 parent
    assert len(chunks) == 2
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    assert leaves[0].page_content == "Q：怎么退货？\nA：提交申请。"
    parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
    assert len(parents) == 1
    assert leaves[0].metadata["parent_chunk_id"] == parents[0].metadata["chunk_id"]


def test_qa_strategy_handles_orphan_answer():
    """孤立的 qa_answer（无配对 question）也保留为 chunk，不丢内容（P1-6）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_answer", text="只有答案。"),
        ]),
    )
    chunks = QAChunkStrategy().split(ast, "faq.md")
    # C1 起：1 leaf + 1 文档级虚拟 parent
    assert len(chunks) == 2
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    assert "只有答案" in leaves[0].page_content


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


def test_faq_real_ast_routes_to_qa_strategy():
    """真实 FAQ AST（无 section，只有 qa 节点）→ 完整链路应走 QAChunkStrategy。

    缺陷 A 根因回归：旧实现下真实 FAQ 的 completeness=0.1，Router 走 Recursive
    兜底，本测试用真实 AST 路径（经 StructureAnalyzer）而非人工 completeness=0.9。
    """
    from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer

    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
            DocumentNode(type="qa_question", text="运费谁付？"),
            DocumentNode(type="qa_answer", text="商家承担。"),
        ]),
        raw_text="怎么退货？\n提交申请。\n运费谁付？\n商家承担。",
    )
    _, report = StructureAnalyzer().analyze(ast)
    assert report.is_complete is True
    strategy = ChunkStrategyRouter().route("faq", report)
    assert isinstance(strategy, QAChunkStrategy)


def test_faq_without_qa_nodes_falls_back_to_recursive():
    """缺陷 D：classify 判 faq 但 AST 无 qa 节点 → fallback Recursive，不产 0 chunk。

    根因：classify_doc_type（文件名/关键词）与 parser 的 QA 识别（looks_like_qa_doc）
    是两套独立逻辑，可能不一致。当 doc_type="faq" 但 AST 无 qa_* 节点时，
    路由到 QAChunkStrategy 会产 0 chunk（数据丢失），应 fallback 递归切分。
    """
    from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer

    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="订单问题", level=1, children=[
                DocumentNode(type="paragraph", text="普通段落内容。"),
            ]),
        ]),
        raw_text="订单问题\n普通段落内容。",
    )
    _, report = StructureAnalyzer().analyze(ast)
    strategy = ChunkStrategyRouter().route("faq", report)
    # v3 结构驱动（2026-10-19）：无 qa 节点但有章节 → 章节切分（比递归更
    # 保结构，同样满足「不产 0 chunk、内容不丢」的原始意图）；完全无结构
    # 才递归（见 test_chunking_router.py 的空结构契约）。
    assert isinstance(strategy, StructureChunkStrategy)
    leaves = strategy.split(ast, "faq_no_qa.docx")
    assert leaves, "章节切分必须产出 chunk（缺陷 D 的内容不丢断言）"


def test_docx_faq_question_survives_cleaning_filter_and_index_identity(tmp_path):
    """独立中文问题以 ASCII 问号结尾时仍须进入 Leaf 和有效 Parent。"""
    from docx import Document as DocxDocument

    from backend.rag.indexing.indexer import apply_index_chunk_identity
    from backend.rag.preprocessing.ast import walk
    from backend.rag.preprocessing.chunking import coverage_audit
    from backend.rag.preprocessing.filter import ChunkFilter
    from backend.rag.preprocessing.pipeline import parse_and_chunk_full
    from backend.rag.retrieval.retrievers import attach_parent_context

    source = DocxDocument()
    for i in range(3):
        source.add_paragraph(f"Q:年假申请流程是什么{i}?")
        source.add_paragraph(
            "A:员工通过人力资源系统提交申请，经直属主管审批后安排休假。"
        )
    path = tmp_path / "faq.docx"
    source.save(path)

    chunks, qc = parse_and_chunk_full(str(path), doc_type_hint="faq")
    questions = [
        node for node in walk(qc["raw_ast"].root)
        if node.type == "qa_question"
    ]
    assert len(questions) == 3
    assert all(node.text.strip() for node in questions)

    filterer = ChunkFilter()
    final_chunks = [
        chunk for chunk in chunks
        if filterer.should_keep(chunk.page_content, chunk.metadata)[0]
    ]
    apply_index_chunk_identity(final_chunks, "faq-doc")

    leaves = [
        chunk for chunk in final_chunks
        if chunk.metadata.get("granularity") == "leaf"
    ]
    parents = {
        chunk.metadata["chunk_id"]: chunk
        for chunk in final_chunks
        if chunk.metadata.get("granularity") == "parent"
    }
    assert len(leaves) == 3
    assert set(parents) == {"faq-doc_0"}
    assert all("年假申请流程是什么" in chunk.page_content for chunk in leaves)
    assert all(
        chunk.metadata.get("parent_chunk_id") in parents
        for chunk in leaves
    )
    assert {chunk.metadata["parent_chunk_id"] for chunk in leaves} == {
        "faq-doc_0"
    }
    recalled = attach_parent_context(
        [leaves[0]],
        lambda ids: [
            parents[parent_id] for parent_id in ids if parent_id in parents
        ],
    )
    assert len(recalled) == 2
    assert "年假申请流程是什么" in recalled[1].page_content
    final_coverage = coverage_audit(qc["normalized_ast"], final_chunks)
    assert final_coverage["uncovered_count"] == 0


def test_short_faq_parent_referenced_by_leaf_is_not_dropped(tmp_path):
    """短问题 Parent 虽短，仍需保留为被 Leaf 引用的检索上下文。"""
    from docx import Document as DocxDocument

    from backend.rag.indexing.indexer import (
        _filter_index_chunks,
        apply_index_chunk_identity,
    )
    from backend.rag.preprocessing.filter import ChunkFilter
    from backend.rag.preprocessing.pipeline import parse_and_chunk_full
    from backend.rag.preprocessing.quality_gate import audit_chunk_integrity
    from backend.rag.retrieval.retrievers import attach_parent_context

    source = DocxDocument()
    source.add_paragraph("Q:如何退款?")
    source.add_paragraph(
        "A:请登录账户提交退款申请，工作人员审核通过后会按原支付渠道退回，"
        "通常在三个工作日内到账。"
    )
    path = tmp_path / "short-faq.docx"
    source.save(path)

    chunks, _qc = parse_and_chunk_full(str(path), doc_type_hint="faq")
    filterer = ChunkFilter()
    final_chunks, filtered_details = _filter_index_chunks(
        chunks, filterer, str(path), "faq",
    )
    assert not filtered_details
    apply_index_chunk_identity(final_chunks, "short-faq-doc")

    integrity = audit_chunk_integrity(final_chunks)
    assert integrity["parent_chunk_count"] == 1
    assert integrity["invalid_parent_reference_count"] == 0
    parent = next(
        chunk for chunk in final_chunks
        if chunk.metadata.get("granularity") == "parent"
    )
    assert parent.metadata["filter_status"] == "kept_referenced_parent"
    assert parent.metadata["parent_filter_override_reason"] == "too_short"
    leaf = next(
        chunk for chunk in final_chunks
        if chunk.metadata.get("granularity") == "leaf"
    )
    recalled = attach_parent_context(
        [leaf], lambda ids: [parent] if parent.metadata["chunk_id"] in ids else [],
    )
    assert len(recalled) == 2
    assert "如何退款" in recalled[1].page_content
