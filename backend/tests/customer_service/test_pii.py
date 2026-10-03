"""C11 PII 脱敏回归（2026-10-03）。

验收口径（演进分析 C11）：
  - 手机号/地址/姓名进 LLM 前脱敏率 100%（20 条 payload 断言无明文 PII）；
  - 回复还原正确率 100%（端到端 mask→LLM模拟→unmask）；
  - 零 PII 消息零开销路径；parity 接线在 knowledge expert。
"""

import re

from backend.customer_service.pii import (
    contains_placeholder,
    mask_pii,
    unmask_text,
)
from backend.customer_service.experts import knowledge as knowledge_expert

# 手机号/身份证（强规则，漏一个即失败）
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# 20 条模拟真实客服进线 payload（验收 C11 抽样口径）
PAYLOADS = [
    "我的手机号 13812345678 收不到验证码",
    "联系人是张伟，电话 15912345678，麻烦尽快回电",
    "我叫李娜，订单 1001 的地址写错了，收货地址是福建省福州市鼓楼区中山路23号3栋502室",
    "身份证 350102199003071234 被占用注册了",
    "邮箱 user.tag@example.com 收不到发票，手机 18687654321 也换过了",
    "收件人：王小明，电话 13599998888，地址浙江省杭州市西湖区文三路100号2幢",
    "我是陈志强，座机 0591-87654321 打不通",
    "把订单发到 上海市浦东新区张江路678号1号楼，收件人赵敏",
    "我电话 17700001111，名字叫欧阳文雨，帮我改手机号",
    "地址福建省福州市台江区江滨路56号 金色维也纳A栋1801，手机 13122223333",
    "座机 010-65542333 和邮箱 a.b-test@corp.com.cn 都是我的",
    "本人姓刘，叫刘德华（重名），单号 789，手机 18811112222",
    "我是Guest用户，但收件手机 15566667777 是我本人的",
    "新地址江苏省南京市玄武区中山陵8号，联系人钱七",
    "原来手机 13012341234 不用了，改成 13098765432",
    "我的身份证号 11010119850505123X 想解绑",
    "收件人 孙丽，13900001111，北京市朝阳区望京街道26号院5号楼",
    "帮我查 13800138000 这个手机号的订单",
    "地址 广东省深圳市南山区科技园南路15栋3单元，电话 15833334444",
    "我叫阿依古丽，手机 19988887777，地址新疆维吾尔自治区乌鲁木齐市天山区和平路1号",
]


def test_mask_20_payloads_no_plain_pii_left():
    """验收 C11：20 条 payload 掩码后无明文手机号/身份证残留。"""
    for text in PAYLOADS:
        masked, vault = mask_pii(text)
        assert not _PHONE.search(masked), f"手机号漏掩: {text} -> {masked}"
        assert not _ID.search(masked), f"身份证漏掩: {text} -> {masked}"
        assert vault, f"应检出至少一个 PII: {text}"


def test_mask_20_payloads_no_raw_fragment_leak():
    """地址/姓名类：掩码后的文本不得残留原文中的地址片与自报姓名。"""
    for text in PAYLOADS:
        masked, vault = mask_pii(text)
        # 抽取 payload 中的自报姓名（若有）并断言不残留
        for name in ("张伟", "李娜", "王小明", "陈志强", "赵敏", "欧阳文雨", "钱七", "孙丽", "阿依古丽"):
            if name in text:
                assert name not in masked, f"姓名漏掩: {text} -> {masked}"
        # 行政区划+街道片（地址正则的核心面）不得残留
        assert not re.search(r"[\u4e00-\u9fa5]{2,8}(?:省|自治区)[\u4e00-\u9fa5]{2,8}市", masked), \
            f"地址漏掩: {masked}"


def test_unmask_restores_100_percent():
    """验收 C11：端到端 mask→(模拟LLM回显)→unmask 还原正确率 100%。"""
    for text in PAYLOADS:
        masked, vault = mask_pii(text)
        # 模拟 LLM 在回复中引用用户原文（整句回显是最严苛场景）
        simulated_llm_reply = f"您提供的信息是：{masked}，已为您记录。"
        restored = unmask_text(simulated_llm_reply, vault)
        assert restored == f"您提供的信息是：{text}，已为您记录。"


def test_unmask_unknown_placeholder_untouched():
    masked, vault = mask_pii("手机 13812345678")
    assert vault
    # 非 vault 签发的占位符不还原（防跨会话串号）
    out = unmask_text("占位 [隐私信息9] 与 [隐私信息1]", vault)
    assert "[隐私信息9]" in out
    assert "13812345678" in out


def test_same_original_maps_same_placeholder():
    masked, vault = mask_pii("手机 13812345678 备用 13812345678")
    assert masked.count("[隐私信息1]") == 2, "同一原文应复用同一占位符"
    assert len(vault) == 1


def test_no_pii_zero_cost_path():
    text = "请问退货政策是什么？"
    masked, vault = mask_pii(text)
    assert masked == text
    assert len(vault) == 0
    assert not contains_placeholder(masked, vault)


def test_id_card_masked_before_phone():
    """18 位身份证不得被手机号规则截走前 11 位。"""
    masked, _vault = mask_pii("身份证 350102199003071234")
    assert "[隐私信息1]" in masked
    assert "350102199003071234" not in masked
    assert "19900307123" not in masked.replace("[隐私信息1]", "")


def test_knowledge_expert_masks_payload_and_restores_reply(monkeypatch):
    """接线 parity：knowledge expert 进服务的 question 已掩码、回复已还原。"""
    captured = {}

    class FakeResult:
        answer = "已记录您的手机号 [隐私信息1]，验证码将重新发送。"
        decision = type("D", (), {"value": "answer"})()
        confidence = 0.9
        kb_ids = ["cs_faq"]
        source_documents = []

    class FakeService:
        def answer(self, question, **kwargs):
            captured["question"] = question
            return FakeResult()

    monkeypatch.setattr(
        "backend.customer_service.knowledge.get_knowledge_service",
        lambda: FakeService(),
    )
    import backend.observability.metrics as metrics
    monkeypatch.setattr(metrics, "record_cs_rag_status", lambda *_a, **_k: None)

    raw = "我手机 13812345678 收不到验证码"
    # ExpertResult 是 TypedDict（Supervisor/Reporter 消费契约）
    result = knowledge_expert.execute_knowledge(raw, {"kb_ids": ["cs_faq"], "intent": "k_faq"}, "s1")
    # 进 LLM/RAG 的 payload 无明文手机号
    assert "13812345678" not in captured["question"]
    assert "[隐私信息" in captured["question"]
    # 回复已还原为用户原文
    assert "13812345678" in result["response_draft"]
    assert "[隐私信息" not in result["response_draft"]
