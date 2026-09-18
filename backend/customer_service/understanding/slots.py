"""slots.py — 槽位规则（intent → 必填槽位 → missing_slots）。

只做「结构性必填槽位」（订单号/换货目标/新地址等）；退款原因等软槽位由
确认卡环节补齐（proposal 阶段追问），不在此处制造模糊判定。
槽位名与评测集 cs-v2 的 entities 键域对齐。
"""
from __future__ import annotations

from backend.customer_service.understanding.types import EntityType

# intent → 必填槽位（槽位名即 EntityType.value 或业务槽位名）
REQUIRED_SLOTS: dict[str, list[str]] = {
    "as_refund": ["order_id"],
    "as_return": ["order_id"],
    "as_exchange": ["order_id", "exchange_target"],
    "as_repair": ["order_id"],
    "as_quality_issue": ["order_id"],
    "a_address": ["order_id", "new_address"],
    "a_password": [],
    "a_login_issue": [],
}

_ENTITY_SLOT = {"order_id": EntityType.ORDER_ID}


def compute_missing_slots(intent: str, entities: list) -> list[str]:
    """返回该意图下缺失的必填槽位（保持 REQUIRED_SLOTS 声明顺序）。"""
    required = REQUIRED_SLOTS.get(intent)
    if not required:
        return []
    have = {e.type.value for e in entities or []}
    missing: list[str] = []
    for slot in required:
        etype = _ENTITY_SLOT.get(slot)
        if etype is not None:
            if etype.value not in have:
                missing.append(slot)
        else:
            # 非实体槽位（exchange_target/new_address）：增量 1 由接线层按
            # 抽取结果填充；本层仅能判「完全无候选」的保守口径，预留参数由
            # 调用方传入已抽取槽位集合。
            missing.append(slot)
    return missing


def compute_missing_slots_with_hints(intent: str, entities: list,
                                     present_slots: set[str] | None = None) -> list[str]:
    """带调用方提示的版本：present_slots 为接线层已确认存在的非实体槽位。"""
    base = [s for s in compute_missing_slots(intent, entities)]
    if not present_slots:
        return base
    return [s for s in base if s not in present_slots]
