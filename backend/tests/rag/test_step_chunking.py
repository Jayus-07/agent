"""test_step_chunking.py — StepChunkStrategy 步骤切分。"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    StepChunkStrategy, _is_section_heading,
)


def test_is_section_heading():
    """中文编号章节标题（一、二、三、）→ True。"""
    assert _is_section_heading("一、供应商准入") is True
    assert _is_section_heading("二、采购下单") is True
    assert _is_section_heading("1. 供应商提交资质") is False
    assert _is_section_heading("普通文本") is False


def test_step_strategy_splits_by_section():
    """按章节标题切分，章节下的步骤合并成一个 chunk。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="一、供应商准入"),
            DocumentNode(type="paragraph", text="1. 提交资质"),
            DocumentNode(type="paragraph", text="2. 初审"),
            DocumentNode(type="paragraph", text="二、采购下单"),
            DocumentNode(type="paragraph", text="采购申请流程"),
        ]),
        raw_text="一、供应商准入\n1. 提交资质\n2. 初审\n二、采购下单\n采购申请流程",
    )
    chunks = StepChunkStrategy().split(ast, "x.docx")
    assert len(chunks) == 2
    assert "供应商准入" in chunks[0].page_content
    assert "提交资质" in chunks[0].page_content
    assert "采购下单" in chunks[1].page_content


def test_step_strategy_no_section_returns_single_chunk():
    """无章节标题 → 全部合并成一个 chunk（不丢失内容）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="1. 步骤一"),
            DocumentNode(type="paragraph", text="2. 步骤二"),
        ]),
        raw_text="1. 步骤一\n2. 步骤二",
    )
    chunks = StepChunkStrategy().split(ast, "x.docx")
    assert len(chunks) == 1
    assert "步骤一" in chunks[0].page_content
    assert "步骤二" in chunks[0].page_content
