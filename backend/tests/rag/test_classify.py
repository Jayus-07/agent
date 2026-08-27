"""test_classify.py — classify_doc_type 大写关键词匹配（lowercase 文本）。"""
import pytest

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


# =====================================================
# 边界样本回归（2026-08-27 实测固化）：多类型关键词交叉竞争时，
# 静态 + 动态词库计分应保持正确裁决。后续改词 / 调权重 / 改仲裁
# 逻辑，跑本组即可发现回归。
# =====================================================

BOUNDARY_SAMPLES = [
    ("sop夹policy词", "标准操作流程：员工按规范提交采购申请，部门审批后方可执行。", "sop"),
    ("产品说明夹培训", "产品参数与规格参数表详见附录。使用前请完成上岗培训与考核。", "product_spec"),
    ("安全夹个人信息", "访问控制策略要求加密存储，个人信息字段必须脱敏，防止数据泄露。", "security"),
    ("制度夹合同词", "员工管理制度规定：对外签订合同须走法务审批流程并留存备份。", "policy"),
    ("财务夹审计", "本季度报销与预算执行情况已提交财务审计。", "financial"),
    ("泛词只有规范", "本规范规定了相关规范的管理规范要求。", "policy"),
]


@pytest.mark.parametrize(
    "name,text,expected",
    BOUNDARY_SAMPLES,
    ids=[s[0] for s in BOUNDARY_SAMPLES],
)
def test_boundary_cross_type_samples(name, text, expected):
    """边界交叉样本：类型 A 文档夹带类型 B 关键词时不得误判。"""
    assert classify_doc_type(text) == expected, f"[{name}] 期望 {expected}"


# =====================================================
# 英文文档覆盖：DOC_TYPE_RULES 英文 pattern 全小写（分类器匹配
# text_lower，静态规则大小写敏感），纯英文文档不得漏判为 general。
# =====================================================

ENGLISH_SAMPLES = [
    ("security", "This document defines the password policy, encryption standards "
                 "and access control procedures for all production systems."),
    ("compliance", "We comply with GDPR, CCPA and SOC 2 requirements. Data protection "
                   "and privacy policy apply to all processing activities."),
    ("financial", "Quarterly budget review: revenue increased while expenses decreased. "
                  "Invoice reimbursement requires manager approval and the balance sheet is attached."),
    ("legal", "This agreement contains terms and conditions including liability, "
              "indemnity and confidentiality clauses for both parties."),
    ("faq", "Frequently asked questions about return policy, shipping and refunds from our customers."),
    ("product_spec", "Datasheet and technical specification for model X200: user manual, "
                     "maintenance guide and parameters table included."),
]


@pytest.mark.parametrize(
    "expected,text",
    ENGLISH_SAMPLES,
    ids=[f"en_{s[0]}" for s in ENGLISH_SAMPLES],
)
def test_english_documents_classified(expected, text):
    """纯英文文档：英文 pattern 生效，分类正确且不是 general。"""
    assert classify_doc_type(text) == expected


def test_product_spec_training_margin_avoids_arbitration():
    """成本优化回归：产品文档夹带「培训/考核」字样时，product_spec 必须以
    超过仲裁阈值（5 分）的分差领先 training，避免每次上传都触发一次
    LLM 仲裁调用。静态规则单独成立（无动态词库环境下也须通过）。
    """
    from backend.rag.preprocessing.metadata import classify_with_confidence

    text = "产品参数与规格参数表详见附录。使用前请完成上岗培训与考核。"
    _, _, detail = classify_with_confidence(text, return_detail=True)
    scores = detail["scores"]
    margin = scores["product_spec"] - scores.get("training", 0)
    assert margin > 5, f"product_spec 对 training 领先不足（分差 {margin}），会触发 LLM 仲裁"
