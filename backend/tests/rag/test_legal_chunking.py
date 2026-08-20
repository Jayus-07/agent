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
    assert len(chunks) == 2
    assert "第一条" in chunks[0].page_content
    assert "第二条" in chunks[1].page_content


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
    assert len(chunks) == 1


def test_legal_routes_to_legal_strategy():
    """legal / contract_template 路由到 LegalChunkStrategy。"""
    from backend.rag.preprocessing.chunking import ChunkStrategyRouter
    from backend.rag.preprocessing.structure_analyzer import StructureReport

    report = StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=0.9,
    )
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
