from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer

STRUCTURED = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="section", text="退货流程", level=1, children=[
            DocumentNode(type="paragraph", text="客服审核退货原因。"),
        ]),
        DocumentNode(type="section", text="差评处理", level=1, children=[
            DocumentNode(type="paragraph", text="48小时内给出方案。"),
        ]),
    ]),
    raw_text="退货流程\n客服审核退货原因。\n差评处理\n48小时内给出方案。",
)

UNSTRUCTURED = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="paragraph", text="客户提交申请后，客服首先核对订单信息。对于特殊商品还需检查退货条件。确认后进入下一阶段。"),
    ]),
    raw_text="客户提交申请后，客服首先核对订单信息。对于特殊商品还需检查退货条件。确认后进入下一阶段。",
)


def test_structured_doc_high_completeness():
    _, report = StructureAnalyzer().analyze(STRUCTURED)
    assert report.is_complete is True
    assert report.deficit_signal == ""


def test_unstructured_doc_low_completeness():
    _, report = StructureAnalyzer().analyze(UNSTRUCTURED)
    assert report.is_complete is False
    assert report.deficit_signal == "no_heading"


def test_empty_doc_zero_completeness():
    empty = DocumentAST(root=DocumentNode(type="section", text="", level=0), raw_text="")
    _, report = StructureAnalyzer().analyze(empty)
    assert report.completeness == 0.0
