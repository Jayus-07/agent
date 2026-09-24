"""travel/booking/identity.py — Booking 稳定标识派生（STOP L1，G6/G11/G12）

三层身份沿用 business_guard 四层模型（migration 051 前例）：
  业务操作身份  = booking_intent_id / merchant_order_id（本轮派生）
  确认绑定      = confirmation_fingerprint（quote 事实的 canonical 指纹）
  幂等 ledger   = client_key=merchant_order_id（shared/idempotency）
  provider key  = derive_provider_key（shared/provider_idempotency，冻结）

全部确定性（uuid5/sha256）：双击、刷新、SSE 重连、HTTP retry、worker retry
构造出**同一个** intent/order/key——不存在「每次执行临时生成」（uuid4 当
key = 没有幂等，红线）。
"""
from __future__ import annotations

import hashlib
import uuid

# uuid5 命名空间（本项目固定；不随进程变化）
_BOOKING_NS = uuid.uuid5(uuid.NAMESPACE_URL, "agent-platform:travel-booking:v1")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_amount(value) -> str:
    """金额规范字符串（Decimal → 去尾零定点表示）。

    NUMERIC(18,4) 回读 `420.0000` 与创建时 `420` 必须规整为同一形态——
    指纹/比较前的唯一入口（§十一/§十二：Decimal 精确比较的配套纪律）。
    """
    from decimal import Decimal

    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(d.normalize(), "f")


def booking_intent_id(*, tenant_id: str, user_id: str, quote_id: str,
                      confirmation_fingerprint: str) -> str:
    """同一 (租户, 用户, Quote, 确认事实) → 同一 intent（双击不产生新意图）。"""
    raw = "|".join((tenant_id, user_id, quote_id, confirmation_fingerprint))
    return str(uuid.uuid5(_BOOKING_NS, f"intent:{raw}"))


def merchant_order_id(*, tenant_id: str, intent_id: str, provider: str) -> str:
    """商户订单号（对外引用/lookup 键）：MOB- + 确定性哈希前缀。"""
    return "MOB-" + _sha(f"{tenant_id}|{intent_id}|{provider}")[:20]


def confirmation_fingerprint(*, quote_id: str, quote_fingerprint: str,
                             amount: str, currency: str, provider: str,
                             operation: str, tenant_id: str, user_id: str) -> str:
    """确认绑定快照指纹（§十一：确认的是具体价格与具体订单事实）。

    金额以 Decimal 字符串原样参与（调用方保证规范形态），禁 float。
    """
    raw = "|".join(("cf1", quote_id, quote_fingerprint, str(amount),
                    currency, provider, operation, tenant_id, user_id))
    return "bcf_" + _sha(raw)[:32]


def quote_fingerprint(*, tenant_id: str, provider: str, offer_fingerprint: str,
                      booking_facts: dict, amount: str, currency: str) -> str:
    """Quote 指纹（§九）：tenant+offer identity+booking facts+金额+币种。

    booking_facts 经 canonical JSON（sorted-keys，与幂等 ledger 同源规范化）。
    """
    import json

    canonical = json.dumps(booking_facts, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    raw = "|".join(("qf1", tenant_id, provider, offer_fingerprint, canonical,
                    str(amount), currency))
    return "bqf_" + _sha(raw)[:32]


def create_request_payload(*, merchant_order_id: str, provider: str,
                           offer_fingerprint: str, commerce_type: str,
                           booking_facts: dict, amount: str, currency: str) -> dict:
    """create 请求 canonical 载荷（幂等 ledger request_hash 的输入）。

    只含业务字段——时间戳/trace/execution 字段不得进入（否则同一逻辑操作
    在重试时指纹漂移 = 幂等失效）。
    """
    return {
        "merchant_order_id": merchant_order_id,
        "provider": provider,
        "offer_fingerprint": offer_fingerprint,
        "commerce_type": commerce_type,
        "booking_facts": booking_facts,
        "amount": str(amount),
        "currency": currency,
    }
