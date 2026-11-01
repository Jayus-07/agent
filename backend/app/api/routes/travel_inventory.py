"""travel_inventory.py — 旅游域意图目录（capability-governance G5 适配器）

``GET /travel/capabilities/inventory``
  域清单协议（tool-skill-guide.md §域清单协议）的 travel 消费者：
  ``{domain, range, total, stats_available, intents:[{intent, description,
  stats:{calls, handoff_rate, unresolved}}]}``

口径（G5 验收的诚实降级）：
  - 意图清单与描述 = backend/travel/core/intent.py 的 TravelIntent +
    INTENT_DESCRIPTIONS（与枚举同文件声明，禁止别处手抄）；
  - **运行时统计待 travel-v2 数据面**（当前无 dashboard/意图打点表）：
    stats 按协议降级为 calls=0 / handoff_rate=None / unresolved=0，
    ``stats_available: false`` 显式标注——不伪造数值；
  - 权限对齐 travel REST 只读口径（require_identity，未认证 401）；
    静态元数据无租户数据闸。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.app.api.identity import require_identity
from backend.travel.core.intent import INTENT_DESCRIPTIONS, TravelIntent

router = APIRouter(tags=["旅游-能力目录"])

_RANGE_DAYS = {"today": 1, "d7": 7, "d30": 30}


@router.get("/travel/capabilities/inventory")
async def travel_capabilities_inventory(
    range_key: str = "d7",
    _identity: Any = Depends(require_identity),
) -> dict[str, Any]:
    """旅游域意图目录（静态清单 + 统计降级标注，运营只读）。"""
    if range_key not in _RANGE_DAYS:
        range_key = "d7"  # 协议固定窗口枚举；非法值回退默认（静态清单无窗口语义）
    intents = [
        {
            "intent": intent.value,
            "description": INTENT_DESCRIPTIONS[intent.value],
            "stats": {"calls": 0, "handoff_rate": None, "unresolved": 0},
        }
        for intent in TravelIntent
    ]
    return {
        "domain": "travel",
        "range": range_key,
        "total": len(intents),
        "stats_available": False,
        "stats_note": "运行时统计待 travel-v2 数据面（当前无意图打点），先交付协议合规的静态清单",
        "intents": intents,
    }


__all__ = ["router"]
