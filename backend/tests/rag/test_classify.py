"""test_classify.py — classify_doc_type 大写关键词匹配（lowercase 文本）。"""
from backend.rag.preprocessing.metadata import classify_doc_type


def test_classify_sop_with_uppercase_keyword():
    """「SOP」大写关键词在 lowercase 文本中能匹配 → 正确分类为 sop。

    根因：classify 用 text.lower()，但 DOC_TYPE_RULES 里 SOP/FAQ/GDPR 等
    是大写 pattern，导致大写关键词全部失效。
    """
    text = "采购流程（SOP）\n文件编号：SOP-PUR-001\n对账付款转财务审核。"
    assert classify_doc_type(text, filename="采购流程.docx") == "sop"


def test_classify_faq_with_uppercase_keyword():
    """「FAQ」大写关键词能匹配 → 分类为 faq。"""
    text = "常见问题 FAQ\n下单后如何修改地址？"
    assert classify_doc_type(text, filename="faq.docx") == "faq"
