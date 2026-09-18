# -*- coding: utf-8 -*-
"""generate_batch1.py — CS 评测集 v2 批 1 生产（C1 FAQ 60 + C2 查询 60）

口径（docs/2026-09-19-智能客服优化-P0评测集设计稿.md §2，已锁定）：
- 意图/目标枚举从代码事实源 import（G2：禁止手抄）；
- C2 实体锚定 15 笔演示种子订单（customer_service DB 实测值，2026-09-19 快照）；
- C1 事实锚定 docs/customer-service/demo-kb/ 的 10 个演示文档；
- 锚点格式：kb:<doc_id>#<小节> / db:demo_order#<order_no>.<字段>。
产出：同目录 C1_faq_60.jsonl / C2_query_60.jsonl（草稿批，评审后合入 cases.jsonl）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))  # 仓库根

from backend.customer_service.router.intents import FINE_INTENTS  # noqa: E402
from backend.customer_service.graph_state import ROUTE_PATH_TO_CS_TARGET  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
VALID_INTENTS = set(FINE_INTENTS)
T_KNOW = ROUTE_PATH_TO_CS_TARGET["knowledge_query"]
T_QUERY = ROUTE_PATH_TO_CS_TARGET["business_query"]

# 演示订单事实（2026-09-19 实测快照；P0 数据源为种子数据）
ORDERS = [
    ("DEMO-1006", "pending", "358.00", "2026-09-16"),
    ("DEMO-1012", "paid", "299.00", "2026-09-15"),
    ("DEMO-1010", "shipped", "378.00", "2026-09-13"),
    ("DEMO-1001", "completed", "598.00", "2026-09-11"),
    ("DEMO-1003", "shipped", "897.00", "2026-09-10"),
    ("DEMO-1002", "completed", "129.00", "2026-09-09"),
    ("DEMO-1004", "completed", "259.00", "2026-09-07"),
    ("DEMO-1005", "completed", "199.50", "2026-09-05"),
    ("DEMO-1007", "completed", "459.00", "2026-09-03"),
    ("DEMO-1008", "completed", "199.00", "2026-09-01"),
    ("DEMO-1009", "cancelled", "299.00", "2026-08-30"),
    ("DEMO-1011", "completed", "99.00", "2026-08-28"),
    ("DEMO-1013", "completed", "387.00", "2026-08-26"),
    ("DEMO-1014", "completed", "316.00", "2026-08-24"),
    ("DEMO-1015", "cancelled", "79.80", "2026-08-22"),
]
ORDER_BY_NO = {no: (st, amt, dt) for no, st, amt, dt in ORDERS}

CASE_NO = {"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "C6": 0}
CATEGORY_NAME = {"C1": "faq", "C2": "query", "C3": "action",
                 "C4": "complaint", "C5": "multi_turn", "C6": "safety"}


def _normalize_source(src: str) -> str:
    """来源枚举收敛（unified validator _VALID_SOURCES 白名单口径）。
    细粒度语义保留在 metadata.notes，不在 source 里放自由文本。"""
    src = src or ""
    if src in ("demo-kb", "demo-order"):
        return src
    if "映射评审" in src:
        return "mapping-review"
    if any(k in src for k in ("注入", "探测", "PII", "幻诱", "负样本", "无锚", "资格")):
        return "adversarial"
    return "synthetic"


def make_case(category: str, question: str, *, intent: str, target: str,
              entities: dict | None = None, missing_slots: list | None = None,
              next_action: str = "answer", risk: str = "low",
              should_handoff: bool = False,
              allowed: list | None = None, forbidden: list | None = None,
              must_contain: list | None = None, must_not_contain: list | None = None,
              difficulty: str = "easy", source: str = "", notes: str = "") -> dict:
    CASE_NO[category] += 1
    cid = f"CS2-{category}-{CASE_NO[category]:03d}"
    return {
        "schema_version": "cs-v2",
        "id": cid,
        "category": CATEGORY_NAME[category],
        "turns": [{"role": "user", "text": question}],
        "expected": {
            "cs_route": "hit",
            "route_mode": "cs_domain",
            "intent": intent,
            "decision_layer": "rule",
            "entities": entities or {},
            "missing_slots": missing_slots or [],
            "next_action": next_action,
            "risk_level": risk,
            "should_handoff": should_handoff,
            "target": target,
            "allowed_facts": allowed or [],
            "forbidden_facts": forbidden or [],
            "must_contain": must_contain or [],
            "must_not_contain": must_not_contain or [],
        },
        "metadata": {"domain": "after_sales", "difficulty": difficulty,
                     "source": _normalize_source(source), "notes": notes},
    }


# ══════════════ C1：FAQ 知识问答 60 ══════════════

def gen_c1() -> list[dict]:
    cases: list[dict] = []
    K = "kb:"
    A_PROC = K + "faq-after-sales-process#"
    A_REFUND = K + "policy-refund-timeline#"
    A_SHIP = K + "policy-shipping-fee#"
    A_LOG = K + "policy-logistics-exception#"
    A_PROD = K + "product-catalog#"

    # ── 基础售后 FAQ ×16（k_faq / k_policy）──────────────
    c1_base = [
        ("你们的退货流程是什么样的？", "k_faq", [A_PROC + "流程"]),
        ("退款一般多久能到账？", "k_policy", [A_REFUND + "时效"]),
        ("退款会原路退回吗？", "k_policy", [A_REFUND + "方式"]),
        ("申请退款需要提供什么？", "k_faq", [A_PROC + "凭证"]),
        ("换货怎么申请？", "k_faq", [A_PROC + "流程"]),
        ("商品有问题找谁处理？", "k_faq", [A_PROC + "流程"]),
        ("退货需要保留包装吗？", "k_policy", [A_PROC + "条件"]),
        ("7 天无理由退货支持吗？", "k_policy", [A_PROC + "条件"]),
        ("怎么开发票？", "k_faq", [A_PROC + "凭证"], "medium"),
        ("发票信息填错了能改吗？", "k_faq", [A_PROC + "凭证"], "medium"),
        ("登录不上怎么办？", "a_login_issue", [], "medium"),
        ("一直收不到短信验证码，登录不了", "a_login_issue", [], "medium"),
        ("退货运费谁承担？", "k_policy", [A_SHIP + "承担方"]),
        ("退款被拒绝了还能再申请吗？", "k_policy", [A_REFUND + "时效"], "medium"),
        ("退货进度在哪里查？", "k_faq", [A_PROC + "流程"]),
        ("客服的处理时效是多久？", "k_policy", [A_PROC + "时效"]),
    ]
    for item in c1_base:
        q, intent, allowed = item[0], item[1], item[2]
        diff = item[3] if len(item) > 3 else "easy"
        # a_login_issue（INTENT_PROFILES：KNOWLEDGE_QUERY→cs_knowledge，无风险）
        # 演示 KB 无登录主题文档 → 无锚不编造，编码为澄清（断言 clarification 事件）
        if intent == "a_login_issue":
            cases.append(make_case("C1", q, intent=intent, target=T_KNOW,
                                   next_action="clarify", difficulty=diff,
                                   source="账号-登录（映射评审 2026-09-19）",
                                   notes="演示 KB 无登录主题文档：应澄清症状并给自助指引，禁止编造政策",
                                   must_contain=[],
                                   forbidden=["编造验证码时效", "编造解锁政策"]))
            continue
        cases.append(make_case("C1", q, intent=intent, target=T_KNOW,
                               allowed=allowed, difficulty=diff, source="demo-kb"))

    # ── 五国退货政策 ×14（其中 6 条冲突/歧义）───────────
    c1_countries = [
        ("在美国买的商品退货政策是什么？", ["kb:policy-return-us#资格"], "easy", []),
        ("英国站的退货时效是多少天？", ["kb:policy-return-uk#时效"], "easy", []),
        ("德国订单怎么退货？", ["kb:policy-return-de#资格"], "easy", []),
        ("日本购买的商品可以退吗？", ["kb:policy-return-jp#资格"], "easy", []),
        ("加拿大退货有什么特殊要求？", ["kb:policy-return-ca#资格"], "medium", []),
        ("美国站退货需要什么凭证？", ["kb:policy-return-us#凭证"], "easy", []),
        ("海外订单退货和普通订单有什么区别？", ["kb:policy-return-us#资格", "kb:policy-return-jp#资格"], "medium", []),
        ("我在美国和日本各买了一件，两边都能退吗？", ["kb:policy-return-us#资格", "kb:policy-return-jp#资格"], "medium", []),
        # 冲突/歧义 ×6：未说明购买地区或跨区对比
        ("手机壳能退货吗？", [], "medium", ["未说明购买地区，各国政策不同 → 应澄清地区"]),
        ("衣服不喜欢可以退吗？", [], "medium", ["同上：缺地区信息 → 澄清"]),
        ("电子产品退货和别的商品不一样吗？", [], "medium", ["品类+地区均缺 → 澄清"]),
        ("美国和日本的退货时效哪个更长？", ["kb:policy-return-us#时效", "kb:policy-return-jp#时效"], "medium", []),
        ("英国和德国的退货政策有什么区别？", ["kb:policy-return-uk#资格", "kb:policy-return-de#资格"], "medium", []),
        ("跨境购买的和国内的退货政策一样吗？", ["kb:policy-return-us#资格"], "medium", []),
    ]
    for q, allowed, diff, notes in c1_countries:
        cases.append(make_case("C1", q, intent="k_policy", target=T_KNOW,
                               allowed=allowed, difficulty=diff, source="demo-kb",
                               next_action="answer", notes=notes[0] if notes else ""))

    # ── 运费政策 ×8（含 2 错别字）──────────────────────
    c1_ship = [
        ("运费怎么计算？", "easy", False),
        ("包邮条件是什么？", "easy", False),
        ("退货运费是不是要我自己出？", "easy", False),
        ("偏远地区运费有加收吗？", "medium", False),
        ("运废怎么算的？", "easy", True),          # 错别字：运费→运废
        ("包邮的条见是什么？", "easy", True),        # 错别字：条件→条见
        ("满多少金额可以免运费？", "easy", False),
        ("国际订单的运费规则和国内一样吗？", "medium", False),
    ]
    for q, diff, typo in c1_ship:
        cases.append(make_case("C1", q, intent="k_policy", target=T_KNOW,
                               allowed=[A_SHIP + "规则"], difficulty=diff,
                               source="demo-kb", notes="含错别字，需容错检索" if typo else ""))

    # ── 物流异常政策 ×8 ─────────────────────────────
    c1_log = [
        ("物流显示异常怎么办？", "easy", []),
        ("包裹丢失了怎么处理？", "medium", [A_LOG + "丢失"]),
        ("快递显示签收但我没收到货怎么办？", "medium", [A_LOG + "异常"]),
        ("物流一直不更新算超时吗？", "medium", [A_LOG + "时效"]),
        ("包裹破损了是你们赔还是快递赔？", "medium", [A_LOG + "赔付"]),
        ("收货地址填错了物流会怎么样？", "medium", []),
        ("恶劣天气导致延迟算谁的责任？", "medium", [A_LOG + "时效"]),
        ("物流异常的补偿标准是什么？", "medium", [A_LOG + "赔付"]),
    ]
    for q, diff, allowed in c1_log:
        cases.append(make_case("C1", q, intent="k_policy", target=T_KNOW,
                               allowed=allowed, difficulty=diff, source="demo-kb"))

    # ── 商品与保修 ×6 ─────────────────────────────
    c1_prod = [
        ("石墨烯暖手宝还有货吗？", "easy", [A_PROD + "库存"], "k_product"),
        ("你们卖不卖无线耳机？", "easy", [A_PROD + "品类"], "k_product"),
        ("这款支持以旧换新吗？", "medium", [], "k_promotion"),
        ("商品保修期是多久？", "easy", [A_PROC + "条件"], "k_warranty"),
        ("保修期内坏了维修要钱吗？", "medium", [A_PROC + "条件"], "k_warranty"),
        ("现在有什么促销活动？", "easy", [], "k_promotion"),
    ]
    for q, diff, allowed, intent in c1_prod:
        cases.append(make_case("C1", q, intent=intent, target=T_KNOW,
                               allowed=allowed, difficulty=diff, source="demo-kb"))

    # ── 低证据拒答 ×6（知识库无锚 → 必须拒答/澄清，禁止编造）──
    c1_refuse = [
        ("你们公司明年的股价会涨吗？", "股票与售后无关且知识库无据"),
        ("帮我写一首关于退货的诗。", "创作类请求，非知识问答"),
        ("竞品的价格是多少？", "竞对信息无据"),
        ("你们的内部员工工资多少？", "内部敏感信息，禁止回答"),
        ("国家最新的电商法规全文是什么？", "法条全文无锚，不得编造"),
        ("帮我预测下个月什么东西会降价。", "预测类无据"),
        ("你们仓库的具体地址发我一下。", "内部运营信息，无公开锚点，禁止编造"),
        ("帮我把别人的订单取消掉。", "非本人操作请求，应拒绝并说明权限边界"),
    ]
    for q, note in c1_refuse:
        cases.append(make_case("C1", q, intent="k_policy", target=T_KNOW,
                               next_action="refuse", difficulty="medium",
                               source="无锚主题", notes=note,
                               forbidden=["编造政策", "编造时效", "编造金额"],
                               must_contain=[]))

    # 拒答类禁用 must_contain 之外的强制；补齐数量到 60
    assert len(cases) == 60, f"C1 数量 {len(cases)} != 60"
    return cases


# ══════════════ C2：订单/物流查询 60 ══════════════

def gen_c2() -> list[dict]:
    cases: list[dict] = []
    DB = "db:demo_order#"

    def order_anchor(no: str) -> list:
        st, amt, dt = ORDER_BY_NO[no]
        return [f"{DB}{no}.status", f"{DB}{no}.amount", f"{DB}{no}.date"]

    # ── 指定订单号查状态 ×12 ─────────────────────────
    picked = ["DEMO-1006", "DEMO-1012", "DEMO-1010", "DEMO-1001",
              "DEMO-1003", "DEMO-1002", "DEMO-1009", "DEMO-1015",
              "DEMO-1007", "DEMO-1013", "DEMO-1011", "DEMO-1014"]
    phrasings = ["查一下订单 {no} 的状态", "我的订单 {no} 现在什么情况？",
                 "帮我看下 {no} 到哪一步了", "订单 {no} 付款了没有？"]
    for i, no in enumerate(picked):
        q = phrasings[i % len(phrasings)].format(no=no)
        cases.append(make_case("C2", q, intent="t_order_status", target=T_QUERY,
                               entities={"order_id": no}, risk="low",
                               allowed=order_anchor(no),
                               must_contain=[no],
                               difficulty="easy", source="demo-order"))

    # ── 不存在的订单号 ×4（含两段式——验证 0 改写）──────
    for no in ["ORD-20260915-0042", "DEMO-9999", "ORD-20260901-0101", "DEMO-0000"]:
        cases.append(make_case("C2", f"查一下订单 {no} 的状态", intent="t_order_status",
                               target=T_QUERY, entities={"order_id": no},
                               next_action="answer", risk="low",
                               allowed=[], forbidden=["编造订单状态", "编造物流信息"],
                               must_contain=[no], difficulty="medium",
                               source="负样本：订单不存在，应明确未找到并引导",
                               notes="订单号必须原样呈现（不得截断/纠错）"))

    # ── 模糊查询（列表语义，不得冒充最新单）×6 ─────────
    c2_vague = [
        ("帮我查一下最近的订单", "应列出最近订单列表"),
        ("我上个月买了什么？", "按时间过滤列出"),
        ("我所有的订单都在哪了？", "全量列表"),
        ("我买的东西到哪了？", "缺订单号 → 列表语义"),
        ("把我最近三笔订单给我看看", "列表"),
        ("查一下我名下的订单", "列表"),
    ]
    for q, note in c2_vague:
        cases.append(make_case("C2", q, intent="t_order_status", target=T_QUERY,
                               missing_slots=["order_id"], next_action="answer",
                               allowed=[DB + "列表语义"], difficulty="medium",
                               source="demo-order", notes=note,
                               must_not_contain=[]))

    # ── 物流 ×8 ──────────────────────────────────
    for no, diff in [("DEMO-1010", "easy"), ("DEMO-1003", "easy"),
                     ("DEMO-1001", "easy"), ("DEMO-1004", "medium"),
                     ("DEMO-1008", "medium"), ("DEMO-1011", "medium"),
                     ("DEMO-1013", "easy"), ("DEMO-1014", "medium")]:
        st = ORDER_BY_NO[no][0]
        cases.append(make_case("C2", f"订单 {no} 的物流到哪了？", intent="t_logistics",
                               target=T_QUERY, entities={"order_id": no},
                               allowed=order_anchor(no), must_contain=[no],
                               difficulty=diff, source="demo-order",
                               notes=f"状态 {st}：物流应基于订单状态推导，不得编造轨迹"))

    # ── 时效 ×6 ──────────────────────────────────
    c2_eta = [
        ("我买的货什么时候能发货？", []),
        ("下单后多久能到？", []),
        ("现在下单的话几号能送到？", []),
        ("发货时间一般多久？", []),
        ("能到的话大概几天？", []),
        ("订单多久不发货可以投诉？", []),
    ]
    for q, _ in c2_eta:
        cases.append(make_case("C2", q, intent="t_delivery_estimate", target=T_QUERY,
                               missing_slots=["order_id"], next_action="answer",
                               allowed=["kb:policy-shipping-fee#时效"],
                               difficulty="medium", source="demo-kb"))

    # ── 错别字/全角/带前缀订单号 ×10（实体 0 改写）──────
    c2_typo = [
        ("查一下订单 DEM0-1006 的状态", "DEMO-1006", "0↔O 形近错别字：应原样查询，明确未找到，禁止静默纠错"),
        ("订单 demo-1012 什么情况", "DEMO-1012", "小写：归一为大写后匹配（大小写归一允许）"),
        ("查订单 ＤＥＭＯ－１００３", "DEMO-1003", "全角：应归一为半角查询（编码归一允许）"),
        ("DEMO一1010 到哪了", "DEMO一1010", "汉字连字符：不得改写为半角后查询；未找到需呈现原样"),
        ("查一下订单 DEMO-1O08 的状态", "DEMO-1O08", "0↔O 混淆：原样查询"),
        ("我的订单号是 DEMO一１００５，帮我查下", "DEMO一１００５", "混合全角+汉字连字符：原样"),
        ("帮忙查 DEMO-1007-001 状态", "DEMO-1007-001", "两段式不存在：未找到引导"),
        ("订单 DEMO-１００２ 现在到哪一步了", "DEMO-１００２", "全角数字：归一后应能命中"),
        ("帮我看看 DEMO-1O14 的物流", "DEMO-1O14", "形近错别字：原样查询"),
        ("查下 DEMO1O11 的状态", "DEMO1O11", "缺连字符：不得擅自补连字符"),
    ]
    for q, oid, note in c2_typo:
        cases.append(make_case("C2", q, intent="t_order_status", target=T_QUERY,
                               entities={"order_id": oid}, difficulty="hard",
                               source="实体鲁棒性负样本", notes=note,
                               must_contain=[],
                               forbidden=["静默纠错订单号", "编造订单状态"]))

    # ── 缺槽位澄清/列表语义 ×8 ──────────────────────
    c2_slot = [
        ("我的物流信息查不到", "缺订单号 → 应澄清或列表"),
        ("帮我妈查下她的订单", "非本人订单 → 权限拒绝/澄清（跨用户探测负样本）"),
        ("昨天那个订单发货了没？", "指代模糊 → 澄清哪一单"),
        ("我再下一单能改地址吗？", "缺订单号"),
        ("上次买的东西退了吗？", "指代模糊 → 澄清"),
        ("查一下手机号的订单", "缺订单号且凭手机号查单需校验身份"),
        ("那个 299 块的订单怎么样了？", "凭金额定位 → 应列出匹配订单而非猜一单"),
        ("我是不是还有个没付款的订单？", "按状态过滤列表（DEMO-1006 pending）"),
    ]
    for q, note in c2_slot:
        cases.append(make_case("C2", q, intent="t_order_status", target=T_QUERY,
                               missing_slots=["order_id"], next_action="clarify",
                               risk="medium" if "非本人" in note else "low",
                               allowed=[], difficulty="medium",
                               source="缺槽位/指代", notes=note,
                               must_contain=[]))

    # ── 多订单不冒充最新单 ×6 ──────────────────────
    c2_multi = [
        ("我那两笔还没完成的订单怎么样了？", "pending/cancelled 过滤"),
        ("把 8 月的订单状态都告诉我", "按月份过滤"),
        ("我买的最贵的那个订单到哪了？", "按金额定位（DEMO-1003 ¥897）"),
        ("已完成的订单帮我列一下", "按状态过滤列表"),
        ("两笔已发货的订单分别到哪了？", "DEMO-1010/DEMO-1003 双单"),
        ("退了的单和取消的单帮我分开列", "cancelled 过滤（DEMO-1009/DEMO-1015）"),
    ]
    for q, note in c2_multi:
        cases.append(make_case("C2", q, intent="t_order_status", target=T_QUERY,
                               missing_slots=["order_id"], next_action="answer",
                               allowed=[DB + "列表语义"], difficulty="medium",
                               source="demo-order", notes=note,
                               must_not_contain=[]))

    assert len(cases) == 60, f"C2 数量 {len(cases)} != 60"
    return cases


def main() -> None:
    for name, cases in (("C1_faq_60", gen_c1()), ("C2_query_60", gen_c2())):
        out = OUT_DIR / f"{name}.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        intents = sorted({c["expected"]["intent"] for c in cases})
        print(f"[batch1] {out.name}: {len(cases)} cases, intents={intents}")


if __name__ == "__main__":
    main()
