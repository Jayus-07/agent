"""travel/commerce/cities.py — Commerce 城市表（verified static mapping）

任务书 §八：城市/机场解析只能来自 Provider 或 **verified static mapping**，
禁止 LLM 造。本表是后者：静态、可枚举、可单测；未收录城市 → 澄清追问
（不猜）。Fake 适配器的机场映射（_CITY_AIRPORT）与本表键对齐。

seed 城市（poi_seed）并入：旅游域种子城市的用户说「在福州找酒店」必须
直接可解析，不允许两套城市口径。
"""
from __future__ import annotations

from functools import lru_cache


# Commerce 扩展城市（spec 示例场景 + 国内主要城市；静态维护，新增加进来）
_COMMERCE_CITIES: tuple[str, ...] = (
    "大阪", "东京", "京都", "奈良", "名古屋", "冲绳", "札幌", "福冈",
    "北京", "上海", "广州", "深圳", "成都", "重庆", "西安", "南京",
    "武汉", "长沙", "昆明", "三亚", "青岛", "大连", "哈尔滨", "洛阳",
)


@lru_cache(maxsize=1)
def known_cities() -> frozenset[str]:
    """seed 城市 ∪ commerce 静态表（去重）。"""
    from backend.tools.travel import poi_seed

    return frozenset(poi_seed.all_cities()) | frozenset(_COMMERCE_CITIES)


def find_city(message: str) -> str | None:
    """消息中出现的第一个已知城市（长名优先，防「大阪」被短名截胡）。"""
    found = [c for c in known_cities() if c in message]
    if not found:
        return None
    found.sort(key=len, reverse=True)
    return found[0]


def find_cities(message: str) -> list[str]:
    """消息中出现的全部已知城市（按出现位置排序；供 A到B 航线解析）。"""
    found = [(message.index(c), c) for c in known_cities() if c in message]
    found.sort()
    return [c for _, c in found]
