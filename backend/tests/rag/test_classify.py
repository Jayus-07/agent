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


# =====================================================
# legal 强特征：「第 N 条」条款编号（实测 04_采购流程.docx 含「合同」
# 字样被误分类为 legal，进而走了 LegalChunkStrategy 产出单一巨型 chunk）。
# =====================================================

def test_legal_score_penalized_without_clause_markers():
    """同样含法律关键词：无「第 N 条」条款编号时 legal 得分必须低于有条款编号的。"""
    from backend.rag.preprocessing.metadata import classify_with_confidence
    base = "甲乙双方签订合同，约定条款、违约责任与赔偿。"
    with_clause = base + "\n第一条 双方义务\n第二条 违约责任\n第三条 赔偿范围"
    _, _, d_no = classify_with_confidence(base, return_detail=True)
    _, _, d_yes = classify_with_confidence(with_clause, return_detail=True)
    assert d_yes["scores"].get("legal", 0) > d_no["scores"].get("legal", 0)


def test_process_doc_with_contract_word_not_misrouted_legal():
    """含「合同」字样的流程类文档不得误判为 legal（真实案例复现）。"""
    text = ("采购流程操作规范：提交采购申请、部门审批、合同归档。"
            "违约责任与赔偿按合同条款执行。")
    assert classify_doc_type(text, filename="采购流程.docx") != "legal"


def test_close_call_arbitration_includes_runner_up(monkeypatch):
    """盲点修复：仲裁候选 top1 仅微弱领先非候选次名时必须仲裁，
    且 sop/financial 等非候选次名进入仲裁候选并可被选中。

    真实案例：04_采购流程.docx 折半后 legal=35/sop=30，旧实现
    close_set 只剩 legal 一个 → 分差再小也不仲裁。
    """
    import backend.rag.preprocessing.metadata as md

    # 计分结果：legal=30(合同×3折半) / sop=30(文件名流程) / policy=10(审批×2)
    text = ("采购申请需审批，采购申请需审批。"
            "合同合同合同条款条款违约责任。")

    invoked = {}

    class _FakeResp:
        content = "sop"

    class _FakeLLM:
        @staticmethod
        def invoke(prompt):
            invoked["prompt"] = prompt
            return _FakeResp()

    # _LLMProxy 是 __slots__ 动态代理，无法 patch 实例属性 → 整体替换模块级 llm
    monkeypatch.setattr(md, "llm", _FakeLLM())
    # 阈值放宽确保触发仲裁分支（验证候选集构成而非具体分差）
    monkeypatch.setattr(md, "_ARBITRATION_THRESHOLD", 100)
    result = md.classify_doc_type(text, filename="采购流程.docx")

    assert "prompt" in invoked, "分差接近时必须触发 LLM 仲裁"
    assert "sop" in invoked["prompt"], "次名 sop 必须进入仲裁候选"
    assert result == "sop"


def test_high_confidence_not_arbitrated(monkeypatch):
    """回归守护：非候选类型大幅领先时不得触发仲裁（08_报销制度 financial=142 场景）。"""
    import backend.rag.preprocessing.metadata as md

    invoked = {}

    class _FakeLLM:
        @staticmethod
        def invoke(prompt):
            invoked["prompt"] = prompt

            class _R:
                content = "legal"
            return _R()

    # financial 大幅领先：财务/预算/发票 多次命中，policy(审批)少量
    text = "财务预算审批。财务预算发票对账，财务预算发票对账。" * 3
    monkeypatch.setattr(md, "llm", _FakeLLM())
    result = md.classify_doc_type(text, filename="报销制度.docx")

    assert "prompt" not in invoked, "高置信分类不应触发 LLM 仲裁"
    assert result == "financial"
