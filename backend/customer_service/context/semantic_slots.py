"""semantic_slots.py — 语义槽位 → 真实业务对象解析（任务书 §九/§十一）。

LLM 产出的语义槽位候选（product_reference/time_reference）在这里用
**真实 user_id** 查业务服务解析成真实订单引用：

  唯一匹配  → order_id 自动绑定（写 cs_route.metadata.order_id 权威 referent）
  多个匹配  → order_candidates 追问点选（绝不自动选最近，P0 golden #4）
  无匹配    → 不猜（返回空 resolution，走既有降级路径）

红线（P0-02/P0-10）：真实 ID 只来自 DB/业务服务/Context Resolver；
本模块是 ID 的**唯一**语义解析出口，LLM 输出永不直接当 ID 消费。
零 LLM：匹配与时间窗口全是规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from backend.shared.logger import logger

_MAX_CANDIDATES = 5

# 时间指称 → (起始偏移天数, 结束偏移天数) 相对今天（含两端）；
# 「最近」「上个月」等不做硬过滤（None = 不缩小）。
_TIME_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("今天", 0, 0),
    ("昨天", 1, 1),
    ("前天", 2, 2),
    ("大前天", 3, 3),
    ("上周", 7, 13),
    ("这周", 0, 6),
    ("本周", 0, 6),
    ("这个月", 0, 30),
    ("这个月", 0, 30),
)


@dataclass
class SemanticSlotResolution:
    """语义槽位解析结果。"""
    order_id: str = ""
    candidates: list[dict] = field(default_factory=list)
    ambiguous: bool = False
    matched_product: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.order_id)


def _parse_time_window(time_ref: str) -> tuple[date, date] | None:
    """时间指称 → 自然日语义窗口；识别不了返回 None（不硬过滤）。"""
    text = (time_ref or "").strip()
    today = datetime.now(timezone.utc).date()
    for token, start_off, end_off in _TIME_WINDOWS:
        if token in text:
            start = today - timedelta(days=start_off)
            end = today - timedelta(days=end_off)
            return (min(start, end), max(start, end))
    return None


def _order_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


def _product_text(order: dict) -> str:
    return str(order.get("product_names") or "").strip()


def resolve_semantic_slots(
    tenant_id: str,
    user_id: str,
    slots: list[dict],
) -> SemanticSlotResolution:
    """按语义槽位候选解析真实订单引用（唯一绑定/多候选追问/无则不猜）。

    任何业务服务故障向上抛 DatabaseError（由专家层按既有「服务不可用」
    话术承接）——不在这里吞成空结果伪装成「没有匹配」。
    """
    product_ref = ""
    time_ref = ""
    for slot in slots or []:
        name = str(slot.get("name", ""))
        if not product_ref and name == "product_reference":
            product_ref = str(slot.get("value", "")).strip()
        if not time_ref and name == "time_reference":
            time_ref = str(slot.get("value", "")).strip()
    if not product_ref or not user_id:
        return SemanticSlotResolution()

    from backend.customer_service.service.order_service import get_order_service

    orders = get_order_service().list_recent_orders_with_products(user_id)

    needle = product_ref.lower()
    matched = [o for o in orders if needle in _product_text(o).lower()]

    # 时间引用只做缩小（弱信号）：商品匹配非空且 >1 时按窗口过滤；
    # 过滤清空则保留原商品匹配集（时间不作为硬性否决——用户对时间的
    # 记忆常不精确，宁可多候选追问也不错误「没有」）。
    if len(matched) > 1 and time_ref:
        window = _parse_time_window(time_ref)
        if window is not None:
            start, end = window
            narrowed = [
                o for o in matched
                if (d := _order_date(o.get("created_at"))) is not None
                and start <= d <= end
            ]
            if narrowed:
                matched = narrowed

    if not matched:
        logger.info(
            "[SemanticSlots] no order matches product_ref=%r user=%s",
            product_ref, user_id,
        )
        return SemanticSlotResolution(matched_product=product_ref)

    if len(matched) == 1:
        order = matched[0]
        order_id = str(order.get("order_no") or order.get("id") or "")
        logger.info(
            "[SemanticSlots] unique bind: order=%s product_ref=%r",
            order_id, product_ref,
        )
        return SemanticSlotResolution(
            order_id=order_id, matched_product=product_ref,
        )

    candidates = [
        {
            "order_id": str(o.get("order_no") or o.get("id") or ""),
            "order_no": str(o.get("order_no") or ""),
            "product_names": _product_text(o) or "商品信息缺失",
            "amount": o.get("total_amount"),
            "status": str(o.get("status") or ""),
            "created_at": str(o.get("created_at") or "")[:10],
        }
        for o in matched[:_MAX_CANDIDATES]
    ]
    logger.info(
        "[SemanticSlots] ambiguous: %d candidates for product_ref=%r → 追问",
        len(matched), product_ref,
    )
    return SemanticSlotResolution(
        candidates=candidates, ambiguous=True, matched_product=product_ref,
    )


def resolve_from_metadata(
    tenant_id: str, user_id: str, cs_route: dict,
) -> SemanticSlotResolution:
    """从 cs_route.metadata.semantic_slots 解析（专家层便捷入口）。"""
    slots = ((cs_route or {}).get("metadata") or {}).get("semantic_slots") or []
    if not slots:
        return SemanticSlotResolution()
    return resolve_semantic_slots(tenant_id, user_id, slots)
