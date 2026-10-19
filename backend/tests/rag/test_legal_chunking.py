"""test_legal_chunking.py — LegalChunkStrategy 合同条款切分。"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    LegalChunkStrategy, _is_legal_clause,
)


def test_is_legal_clause():
    """「第 N 条」条款编号（中文/阿拉伯数字）→ True。"""
    assert _is_legal_clause("第一条 合同双方") is True
    assert _is_legal_clause("第2条 违约责任") is True
    assert _is_legal_clause("第十二条 保密义务") is True
    assert _is_legal_clause("一、适用范围") is False  # 章节，不是条款
    assert _is_legal_clause("普通文本") is False


def test_legal_strategy_splits_by_clause():
    """按「第 N 条」切分，条款内容合并成一个 chunk。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="第一条 合同双方"),
            DocumentNode(type="paragraph", text="甲方与乙方就..."),
            DocumentNode(type="paragraph", text="第二条 违约责任"),
            DocumentNode(type="paragraph", text="违约方应赔偿..."),
        ]),
        raw_text="第一条 合同双方\n甲方与乙方就...\n第二条 违约责任\n违约方应赔偿...",
    )
    chunks = LegalChunkStrategy().split(ast, "x.txt")
    # C1 起：2 条款 leaf + 1 个虚拟 parent（2 条 < 5 条/组 → 单 parent）
    assert len(chunks) == 3
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    assert "第一条" in leaves[0].page_content
    assert "第二条" in leaves[1].page_content
    parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
    assert len(parents) == 1
    assert all(c.metadata["parent_chunk_id"] == parents[0].metadata["chunk_id"]
               for c in leaves)


def test_legal_large_table_splits_by_row_groups(monkeypatch):
    """长表按行组切分，重复表头并完整保留结构化行。"""
    import json

    import backend.rag.preprocessing.chunking as chunking
    from backend.rag.preprocessing.chunking import coverage_audit

    monkeypatch.setattr(chunking, "LEAF_CHUNK_TOKENS", 180)
    rows = [["项目", "单价", "数量", "小计"]] + [
        [f"项目{index:02d}", str(index * 10), "1", str(index * 10)]
        for index in range(1, 31)
    ]
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="技术服务合同", level=1, children=[
                DocumentNode(type="section", text="第一条 服务费用", level=2, children=[
                    DocumentNode(type="paragraph", text="费用见下表。"),
                    DocumentNode(type="table", text="", rows=rows),
                ]),
            ]),
        ]),
        raw_text="",
    )

    chunks = LegalChunkStrategy().split(ast, "long-contract.docx")
    leaves = [
        chunk for chunk in chunks
        if chunk.metadata.get("granularity") == "leaf"
        and chunk.metadata.get("table_id") == "table_1"
    ]
    assert 1 < len(leaves) < len(rows) - 1
    assert all(chunk.metadata["chunk_type"] == "table_row_group" for chunk in leaves)
    assert all(chunk.metadata["chunk_tokens"] <= 180 for chunk in leaves)
    assert all("项目" in chunk.page_content for chunk in leaves)
    assert all(chunk.metadata["row_start"] <= chunk.metadata["row_end"] for chunk in leaves)
    coverage = coverage_audit(ast, chunks)
    assert coverage["uncovered_count"] == 0, coverage

    recovered_rows = [
        row
        for chunk in leaves
        for row in json.loads(chunk.metadata["table_rows_json"])
        if row != rows[0]
    ]
    assert recovered_rows == rows[1:]


def test_legal_strategy_no_clause_single_chunk():
    """无条款编号 → 合并成一个 chunk（不丢内容）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="合同正文"),
            DocumentNode(type="paragraph", text="无条款编号"),
        ]),
        raw_text="合同正文\n无条款编号",
    )
    chunks = LegalChunkStrategy().split(ast, "x.txt")
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    assert len(leaves) == 1


def test_legal_routes_to_legal_strategy():
    """legal / contract_template with clause nodes → LegalChunkStrategy。"""
    from backend.rag.preprocessing.chunking import ChunkStrategyRouter
    from backend.rag.preprocessing.structure_analyzer import StructureReport

    # WS8 eligibility gate requires actual clause nodes in AST
    ast_with_clauses = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="第一条 合同双方"),
            DocumentNode(type="paragraph", text="甲方与乙方..."),
        ]),
        raw_text="第一条 合同双方\n甲方与乙方...",
    )
    report = StructureReport(ast=ast_with_clauses, completeness=0.9)
    r = ChunkStrategyRouter()
    assert isinstance(r.route("legal", report), LegalChunkStrategy)
    assert isinstance(r.route("contract_template", report), LegalChunkStrategy)


# =====================================================
# 无条款降级：误分类为 legal 但内容无「第 N 条」时，
# 不能把全文合成单一巨型 chunk（实测 04_采购流程.docx 466 字 → 1 chunk）。
# =====================================================

def test_legal_no_clause_with_sections_falls_back_to_structure():
    """无条款但有章节结构 → 按结构切分（多 chunk + parent），不合并成巨型单 chunk。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="一、适用范围", level=1, children=[
                DocumentNode(type="paragraph", text="本制度适用于采购流程。"),
            ]),
            DocumentNode(type="section", text="二、操作规则", level=1, children=[
                DocumentNode(type="paragraph", text="采购需经审批后执行。"),
            ]),
        ]),
        raw_text="",
    )
    chunks = LegalChunkStrategy().split(ast, "x.txt")
    assert len(chunks) > 1, "无条款时不应把全文合成单一 chunk"
    assert any(c.metadata["granularity"] == "parent" for c in chunks)


def test_legal_no_clause_flat_keeps_all_content():
    """无条款无章节 → 递归兜底，不丢内容。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="正文内容一。"),
            DocumentNode(type="paragraph", text="正文内容二。"),
            DocumentNode(type="paragraph", text="正文内容三。"),
        ]),
        raw_text="",
    )
    chunks = LegalChunkStrategy().split(ast, "x.txt")
    joined = "\n".join(c.page_content for c in chunks)
    for t in ("正文内容一。", "正文内容二。", "正文内容三。"):
        assert t in joined
