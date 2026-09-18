"""test_entity_eval_200.py — P1 完成标准：200 实体样本精确匹配率 ≥0.95、0 改写。

确定性生成（random.Random 固定种子），无 LLM / 无 IO；口径：
- 允许的归一：NFKC 全角→半角（normalize_query 事实源）、大小写归一（match_value）；
- 禁止的改写：形近错别字（0↔O）必须原样保留（value == match_value == 原样）。
"""
import random

import pytest

from backend.customer_service.understanding import build_understanding
from backend.customer_service.understanding.types import EntityType
from backend.security.input_guard.normalize import normalize_query

SEED_ORDERS = [
    "DEMO-1001", "DEMO-1002", "DEMO-1003", "DEMO-1004", "DEMO-1005",
    "DEMO-1006", "DEMO-1007", "DEMO-1008", "DEMO-1009", "DEMO-1010",
    "DEMO-1011", "DEMO-1012", "DEMO-1013", "DEMO-1014", "DEMO-1015",
]
TEMPLATES = [
    "查一下订单 {oid} 的状态",
    "我的订单 {oid} 到哪了",
    "订单 {oid} 什么时候发货",
    "帮我看看 {oid}",
]


def _to_fullwidth(s: str) -> str:
    return "".join(chr(ord(ch) + 0xFEE0) if 0x21 <= ord(ch) <= 0x7E else ch
                   for ch in s)


def _to_lookalike(s: str) -> str:
    """O↔0 形近替换（仅首个可替换字符，保持合法订单号形态）。"""
    for i, ch in enumerate(s):
        if ch == "O":
            return s[:i] + "0" + s[i + 1:]
        if ch == "0":
            return s[:i] + "O" + s[i + 1:]
    return s


def _gen_samples() -> list[dict]:
    """确定性生成 200 个实体样本。每条：
    text / etype / expected（允许归一后的期望匹配值）/ expect_absent /
    no_correction（True ⇒ 原样值必须与期望一致且不得被纠正）。
    """
    rng = random.Random(20260919)
    samples: list[dict] = []

    # 1) 种子订单 ×4 变体 = 60
    for oid in SEED_ORDERS:
        for kind in ("clean", "lower", "fullwidth", "lookalike"):
            text_oid = {"clean": oid, "lower": oid.lower(),
                        "fullwidth": _to_fullwidth(oid),
                        "lookalike": _to_lookalike(oid)}[kind]
            tmpl = TEMPLATES[rng.randrange(len(TEMPLATES))]
            # 零改写口径：形近错别字的期望值 = 错别字原样（不得纠正为干净形态）；
            # 全角/小写属于允许的归一（NFKC/大小写），期望 = 干净形态
            expected = text_oid if kind == "lookalike" else oid
            samples.append({
                "text": tmpl.format(oid=text_oid),
                "etype": "order_id",
                "expected": expected,
                "no_correction": kind == "lookalike",
            })

    # 2) 两段式合成单号 ×4 变体 = 60
    for i in range(15):
        oid = f"ORD-20260915-{i + 1:04d}"
        for kind in ("clean", "lower", "fullwidth", "lookalike"):
            text_oid = {"clean": oid, "lower": oid.lower(),
                        "fullwidth": _to_fullwidth(oid),
                        "lookalike": _to_lookalike(oid)}[kind]
            expected = text_oid if kind == "lookalike" else oid
            samples.append({
                "text": f"订单 {text_oid} 的物流到哪了",
                "etype": "order_id",
                "expected": expected,
                "no_correction": kind == "lookalike",
            })

    # 3) 不存在订单 ×20（负样本：仍需原样抽取并呈现）
    for i in range(20):
        oid = f"DEMO-9{9 - i % 9}{i:02d}"
        samples.append({"text": f"查一下订单 {oid} 的状态", "etype": "order_id",
                        "expected": oid, "no_correction": False})

    # 4) 金额 ×20
    for i in range(20):
        amt = f"{rng.randrange(10, 900)}.{rng.choice(['00', '50', '99'])}"
        mark = rng.choice(["元", "块钱", "¥"])
        samples.append({"text": f"退 {amt}{mark} 到原卡", "etype": "amount",
                        "expected": amt, "no_correction": False})

    # 5) 手机号 ×10
    for i in range(10):
        ph = f"138{rng.randrange(10000000, 99999999)}"
        samples.append({"text": f"我的手机号 {ph} 能改绑吗", "etype": "phone",
                        "expected": ph, "no_correction": False})

    # 6) 邮箱 ×10
    for i in range(10):
        em = f"user{i}@example.com"
        samples.append({"text": f"发票发到 {em}", "etype": "email",
                        "expected": em, "no_correction": False})

    # 7) 快递单号 ×10
    for _ in range(10):
        tk = f"SF{rng.randrange(10**13, 10**14 - 1)}"
        samples.append({"text": f"快递单号 {tk} 到哪了", "etype": "tracking_no",
                        "expected": tk, "no_correction": False})

    # 8) 无实体负样本 ×10
    for q in ["你们的退货政策是什么", "退款多久到账", "怎么开发票",
              "运费怎么算", "会员积分怎么用", "保修期多久", "人工客服在哪",
              "转人工", "我要投诉", "谢谢"]:
        samples.append({"text": q, "etype": "none", "expected": "",
                        "no_correction": False})

    assert len(samples) == 200, f"样本数 {len(samples)} != 200"
    return samples


_SAMPLES = _gen_samples()


def _extract(text: str, etype: str) -> list[str]:
    normalized = normalize_query(text)
    u = build_understanding(text)
    assert u.normalized_text == normalized
    return [e.match() if etype == "order_id" else e.value
            for e in u.entities if e.type.value == etype]


class TestEntityEval200:
    def test_exact_match_rate_at_least_095(self):
        hits = 0
        misses = []
        for s in _SAMPLES:
            if s["etype"] == "none":
                hits += 1  # 负样本单独断言
                continue
            got = _extract(s["text"], s["etype"])
            if s["expected"] in got:
                hits += 1
            else:
                misses.append((s["text"], got, s["expected"]))
        rate = hits / len(_SAMPLES)
        assert rate >= 0.95, f"实体精确匹配率 {rate:.3f} < 0.95；未命中 {len(misses)}: {misses[:8]}"

    def test_zero_rewrite_violation(self):
        """0 改写：形近错别字样本的原样值必须完整保留（不得被纠正）。"""
        violations = []
        for s in _SAMPLES:
            if not s["no_correction"]:
                continue
            got = _extract(s["text"], s["etype"])
            # match() 走大小写归一；value 必须等于规范化输入中的原样子串。
            # 用「规范化文本包含期望串」判定：若被纠正（0→O），原样串不再出现。
            if s["expected"] not in got:
                violations.append((s["text"], got))
            normalized = normalize_query(s["text"])
            expected_verbatim = _verbatim_in(normalized, s)
            if not expected_verbatim:
                violations.append((s["text"], "原样子串缺失"))
        assert not violations, f"改写违规 {len(violations)} 处: {violations[:5]}"

    def test_negative_samples_extract_nothing(self):
        for s in _SAMPLES:
            if s["etype"] == "none":
                u = build_understanding(s["text"])
                leaked = [e.type.value for e in u.entities
                          if e.type in (EntityType.ORDER_ID, EntityType.PHONE,
                                        EntityType.EMAIL)]
                assert not leaked, f"负样本误抽取: {s['text']} → {leaked}"


def _verbatim_in(normalized: str, s: dict) -> bool:
    """形近错别字样本：规范化文本里必须仍含原样 token（即未被纠正）。
    expected 是归一后的合法形态；原样 token = 从文本里反查正则命中值。
    """
    from backend.customer_service.understanding.entities import extract_entities
    for e in extract_entities(normalized):
        if e.type.value == s["etype"] and e.match() == s["expected"]:
            # 命中期望值时，value 必须与 match 一致或仅大小写差异——
            # 若发生 0/O 纠正，value 将等于纠正形态而文本子串不含它。
            return e.value in normalized
    return False
