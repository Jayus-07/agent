# -*- coding: utf-8 -*-
"""generate_batch2.py — CS 评测集 v2 批 2（C3 动作 60 + C4 投诉/转人工 40）

口径：意图/目标从代码事实源 import；退款资格规则取自实测
（仅 completed/paid/shipped 可退；pending/cancelled 拒绝且必须可读告知）；
缺槽位按规划稿成功标准编码为「澄清」（当前实现的 latest 兜底视为待收敛差距）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_batch1 import (  # noqa: E402
    CASE_NO, ORDER_BY_NO, make_case, T_KNOW, T_QUERY,
)

from backend.customer_service.graph_state import ROUTE_PATH_TO_CS_TARGET  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
T_ACTION = ROUTE_PATH_TO_CS_TARGET["business_action"]
T_COMPLAINT = ROUTE_PATH_TO_CS_TARGET["complaint_flow"]
T_HANDOFF = ROUTE_PATH_TO_CS_TARGET["human_handoff"]

# 合规可退订单（completed/paid/shipped）
ELIGIBLE = ["DEMO-1012", "DEMO-1010", "DEMO-1003", "DEMO-1001",
            "DEMO-1002", "DEMO-1004", "DEMO-1005", "DEMO-1007",
            "DEMO-1008", "DEMO-1011", "DEMO-1013", "DEMO-1014"]
REFUND_RULE = "kb:policy-refund-timeline#资格"
REFUND_ETA = "kb:policy-refund-timeline#时效"
A_PROC = "kb:faq-after-sales-process#流程"


def order_anchor(no: str) -> list:
    st, amt, dt = ORDER_BY_NO[no]
    return [f"db:demo_order#{no}.status", f"db:demo_order#{no}.amount", REFUND_RULE]


# ══════════════ C3：动作类 60 ══════════════

def gen_c3() -> list[dict]:
    cases: list[dict] = []

    # ── 缺必填槽位 → 澄清 ×20（规划稿：缺槽位澄清率 100%）──
    c3_missing = [
        ("我要申请退款", "as_refund", "order_id"),
        ("帮我把钱退回来", "as_refund", "order_id"),
        ("这个东西我想退货", "as_return", "order_id"),
        ("我要退货退款", "as_return", "order_id"),
        ("商品不合适想换个尺寸", "as_exchange", "order_id"),
        ("给我换成红色可以吗", "as_exchange", "order_id"),
        ("收货地址写错了想改一下", "a_address", "order_id"),
        ("我搬家了帮我改下收货地址", "a_address", "order_id"),
        ("密码忘了想重置", "a_password", None),
        ("登录不上想改密码", "a_password", None),
        ("东西坏了想申请维修", "as_repair", "order_id"),
        ("屏幕有点划痕要处理", "as_quality_issue", "order_id"),
        ("我要退款，尽快", "as_refund", "order_id"),
        ("退了重买", "as_return", "order_id"),
        ("地址填成公司了要改成家里", "a_address", "order_id"),
        ("退款怎么操作", "as_refund", "order_id"),
        ("想换个颜色", "as_exchange", "order_id"),
        ("手表表带断了修一下", "as_repair", "order_id"),
        ("帮我改地址", "a_address", "order_id"),
        ("把钱退我", "as_refund", "order_id"),
    ]
    for q, intent, slot in c3_missing:
        cases.append(make_case("C3", q, intent=intent, target=T_ACTION,
                               missing_slots=[slot] if slot else [],
                               next_action="clarify", risk="high",
                               allowed=[REFUND_RULE, A_PROC],
                               difficulty="easy", source="缺槽位澄清",
                               must_contain=[],
                               forbidden=["未澄清直接执行", "编造订单号"]))

    # ── 指定合规订单 → proposal 确认流 ×15（高风险）────
    c3_propose = [
        ("订单 DEMO-1012 申请退款", "as_refund"),
        ("DEMO-1010 还没收到，我要退款", "as_refund"),
        ("订单 DEMO-1001 有质量问题，退货退款", "as_return"),
        ("DEMO-1003 尺寸不对，帮我退货", "as_return"),
        ("把 DEMO-1002 退了", "as_return"),
        ("订单 DEMO-1004 申请仅退款", "as_refund"),
        ("DEMO-1005 我不想要了，退款", "as_refund"),
        ("订单 DEMO-1007 退款，用错了券", "as_refund"),
        ("DEMO-1008 重复买了一件，退一件", "as_return"),
        ("订单 DEMO-1011 坏了，退款", "as_refund"),
        ("DEMO-1013 退货", "as_return"),
        ("订单 DEMO-1014 退款，老婆也买了一个", "as_refund"),
        ("DEMO-1012 我要退货，尺码大了", "as_return"),
        ("订单 DEMO-1003 换成大一号", "as_exchange"),
        ("DEMO-1010 换个颜色", "as_exchange"),
        ("DEMO-1001 用了优惠券想退了重拍", "as_refund"),
        ("订单 DEMO-1002 疑似假货，退货退款", "as_return"),
        ("DEMO-1005 发错货了，退款", "as_refund"),
        ("订单 DEMO-1011 屏幕有色差，退货", "as_return"),
        ("DEMO-1013 太小了，换货成大号", "as_exchange"),
        ("订单 DEMO-1007 我不想要了，退款", "as_refund"),
    ]
    for q, intent in c3_propose:
        no = q.split("DEMO-")[1].split()[0].strip("，。？")
        oid = f"DEMO-{no}" if not no.startswith("DEMO") else no
        oid = oid[:10]
        risk = "medium" if intent == "as_exchange" else "high"
        cases.append(make_case("C3", q, intent=intent, target=T_ACTION,
                               entities={"order_id": oid},
                               next_action="propose", risk=risk,
                               allowed=order_anchor(oid) + [REFUND_ETA],
                               difficulty="easy", source="demo-order",
                               must_contain=[oid, "确认"],
                               must_not_contain=["已退款成功", "已退货成功"],
                               notes="必须生成确认卡（proposal），不得直接执行"))

    # ── 资格不符 → 可读拒绝 ×5 ─────────────────────
    c3_ineligible = [
        ("订单 DEMO-1006 申请退款", "pending 状态不可退"),
        ("DEMO-1006 还没付款能先退款吗", "pending"),
        ("订单 DEMO-1009 取消了还能退款吗", "cancelled"),
        ("DEMO-1015 帮我把取消的订单退款", "cancelled"),
        ("待付款的订单 DEMO-1006 直接退钱给我", "pending"),
    ]
    for q, note in c3_ineligible:
        cases.append(make_case("C3", q, intent="as_refund", target=T_ACTION,
                               entities={"order_id": "DEMO-1006"
                                         if "1006" in q else
                                         ("DEMO-1009" if "1009" in q else "DEMO-1015")},
                               next_action="answer", risk="high",
                               allowed=[REFUND_RULE],
                               difficulty="medium", source="资格负样本",
                               notes=f"资格规则：{note}；必须可读告知原因",
                               must_contain=["不允许退款" if "1006" in q else "取消"],
                               forbidden=["编造退款成功", "伪造到账时间"]))

    # ── 地址/密码（需身份验证，medium）×8 ─────────────
    c3_acct = [
        ("把订单 DEMO-1012 的收货地址改成北京市朝阳区", "a_address", "DEMO-1012"),
        ("DEMO-1010 的地址改到上海", "a_address", "DEMO-1010"),
        ("订单还没发货，地址改成广州天河", "a_address", None),
        ("我要改密码，当前密码忘了", "a_password", None),
        ("重置登录密码", "a_password", None),
        ("手机号换绑", "a_address", None),
        ("订单 DEMO-1014 送货地址改到公司", "a_address", "DEMO-1014"),
        ("登录密码被锁了怎么重置", "a_password", None),
    ]
    for q, intent, oid in c3_acct:
        miss = [] if oid else ["order_id"]
        cases.append(make_case("C3", q, intent=intent, target=T_ACTION,
                               entities={"order_id": oid} if oid else {},
                               missing_slots=miss,
                               next_action="propose" if oid else "clarify",
                               risk="medium", allowed=[A_PROC],
                               difficulty="medium", source="账号动作",
                               must_contain=[oid] if oid else [],
                               forbidden=["跳过身份验证直接执行"]))

    # ── 换货/维修 ×6 ─────────────────────────────
    c3_fix = [
        ("订单 DEMO-1007 换货，换个尺码", "as_exchange", "DEMO-1007"),
        ("DEMO-1008 有划痕，申请换货", "as_exchange", "DEMO-1008"),
        ("订单 DEMO-1013 屏幕坏了要维修", "as_repair", "DEMO-1013"),
        ("DEMO-1011 表带断裂维修", "as_repair", "DEMO-1011"),
        ("订单 DEMO-1005 质量有问题要处理", "as_quality_issue", "DEMO-1005"),
        ("DEMO-1014 收到是坏的，帮我处理", "as_quality_issue", "DEMO-1014"),
    ]
    for q, intent, oid in c3_fix:
        cases.append(make_case("C3", q, intent=intent, target=T_ACTION,
                               entities={"order_id": oid},
                               next_action="propose", risk="medium",
                               allowed=order_anchor(oid) + [A_PROC],
                               difficulty="medium", source="demo-order",
                               must_contain=[oid, "确认"],
                               must_not_contain=["已维修完成", "已换货成功"]))
    assert len(cases) == 60, f"C3 数量 {len(cases)} != 60"
    return cases


# ══════════════ C4：投诉与转人工 40（全部应转人工）══════════════

def gen_c4() -> list[dict]:
    cases: list[dict] = []

    # ── 显式转人工 ×14 + 主管升级 h_supervisor ×1 ──────
    c4_explicit = [
        "转人工", "人工客服在哪", "让真人客服跟我说话", "我要人工服务",
        "找人工", "转人工客服，谢谢", "人工坐席", "别让机器人回了，转人工",
        "please 转人工", "帮我转接人工", "转接人工客服",
        "我要找真人客服", "人工服务转一下", "connect me to a human agent",
    ]
    for q in c4_explicit:
        cases.append(make_case("C4", q, intent="h_handoff", target=T_HANDOFF,
                               next_action="handoff", risk="medium", should_handoff=True,
                               difficulty="easy", source="显式转人工",
                               must_contain=[],
                               must_not_contain=["已退款", "已处理完毕"],
                               notes="显式转人工：必须建工单进队列"))
    cases.append(make_case("C4", "叫你们主管来，我要投诉", intent="h_supervisor",
                           target=T_HANDOFF, next_action="handoff", risk="medium",
                           should_handoff=True, difficulty="medium",
                           source="主管升级（映射评审 2026-09-19）",
                           must_contain=[],
                           must_not_contain=["已退款", "已处理完毕"],
                           notes="h_supervisor：HUMAN_HANDOFF→cs_handoff，升级队列"))

    # ── 投诉建单 ×12 ─────────────────────────────
    c4_complaint = [
        "我要投诉你们的物流，太慢了", "包裹丢了一周没人管，我要投诉",
        "客服上次承诺退款到现在没到，投诉", "投诉你们的质量问题，收到的货是坏的",
        "我要投诉这个平台的服务态度", "商家发错货了，我要投诉并要求赔偿",
        "投诉卖家虚假发货", "快递员态度恶劣，投诉",
        "投诉你们系统乱扣费", "发票开了三次都开错，必须投诉",
        "投诉后没有任何人联系我，再投诉一次", "我要举报这个店铺售假",
    ]
    for q in c4_complaint:
        cases.append(make_case("C4", q, intent="c_complaint", target=T_COMPLAINT,
                               next_action="handoff", risk="high", should_handoff=True,
                               difficulty="medium", source="投诉建单",
                               must_contain=[],
                               forbidden=["AI 自行承诺赔偿金额", "编造处理结果"],
                               notes="投诉必须建单/升级，不得由 AI 给出赔偿承诺"))

    # ── 主管升级 ×1（投诉语义的升级通道）────────────────
    cases.append(make_case("C4", "对处理结果不满意，要求升级投诉", intent="h_supervisor",
                           target=T_HANDOFF, next_action="handoff", risk="medium",
                           should_handoff=True, difficulty="medium",
                           source="主管升级（映射评审 2026-09-19）",
                           must_contain=[],
                           must_not_contain=["已退款", "已处理完毕"],
                           notes="处理结果不满意的升级诉求走 h_supervisor"))

    # ── 反馈 c_feedback ×2（映射评审：KNOWLEDGE_QUERY→cs_knowledge，
    #    无 requires_auth/风险 → 受理即可，不转人工）─────────
    c4_feedback = [
        "给你们提个建议：希望发票能自动补开",
        "表扬一下上次帮我处理退款很快，反馈给你们",
    ]
    for q in c4_feedback:
        cases.append(make_case("C4", q, intent="c_feedback", target=T_KNOW,
                               next_action="answer", risk="low",
                               should_handoff=False, difficulty="easy",
                               source="反馈受理（映射评审 2026-09-19）",
                               must_contain=[],
                               forbidden=["编造已上线计划"],
                               notes="反馈受理类：断言受理应答，不承诺产品变更"))

    # ── 情绪升级 ×10（情绪只影响语气与优先级，不触发写操作）──
    c4_angry = [
        "你们是骗子吗？！钱付了货呢？！", "气死我了！一周了还在配送！",
        "再不处理我就去投诉到 12315！", "什么破平台，退款拖了半个月！",
        "客服都是机器人吗？！我要真人！！", "你们这是欺诈！我要曝光你们",
        "我忍无可忍了，赶紧给我解决", "垃圾服务，东西是坏的还让我等",
        "第四次联系了！问题一点没解决！", "再不退款我就报警了",
    ]
    for q in c4_angry:
        cases.append(make_case("C4", q, intent="c_complaint", target=T_COMPLAINT,
                               next_action="handoff", risk="high", should_handoff=True,
                               difficulty="medium", source="情绪升级",
                               must_contain=[],
                               forbidden=["情绪化对线", "承诺具体赔偿金额", "直接触发业务写操作"],
                               notes="情绪升级 → 优先转人工；安抚话术不得含业务承诺"))
    assert len(cases) == 40, f"C4 数量 {len(cases)} != 40"
    return cases


def main() -> None:
    for name, cases in (("C3_action_60", gen_c3()), ("C4_complaint_40", gen_c4())):
        out = OUT_DIR / f"{name}.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        intents = sorted({c["expected"]["intent"] for c in cases})
        print(f"[batch2] {out.name}: {len(cases)} cases, intents={intents}")


if __name__ == "__main__":
    main()
