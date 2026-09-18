"""travel_prefilter.py — Router 内的旅游域预过滤（与 cs_prefilter 同层）

职责：在进入三层主 Router 之前，用廉价规则判断「这是不是一个旅游规划请求」。
命中则短路进旅游域图，避免旅游请求被主 Router 判成 rag.search 而去知识库
找行程（这正是需要一个独立域图的原因）。

契约（与 cs_prefilter 一致）：
  - 只回答「是不是旅游规划请求」，不回答「去哪几个景点」——后者是域图
    内部 poi/transit 专家的职责
  - 任何异常向上抛出，由 router_node 兜底回退主 Router

判定口径（保守优先）：
  命中 ≥ TRAVEL_DETECT_MIN_HITS 个旅游强信号词；或
  出现了已知城市名，且（命中 1 个信号词 或 出现「城市+天数」组合）。
  「近 3 天」这类业务时间窗被负向后顾断言排除，单靠「周末」「出游」
  这类口语词也不判旅游，否则「周末订单量」会被误路由。
"""
from __future__ import annotations

import re

from backend.shared.logger import logger
from backend.tools.travel import poi_seed

# 旅游规划强信号（正则，与 rule_router 的写法保持一致）
_TRAVEL_PATTERNS: tuple[str, ...] = (
    r"行程", r"攻略", r"旅游", r"旅行", r"自由行", r"自驾游", r"出游",
    r"\d{1,2}\s*天\s*\d{0,2}\s*晚", r"\d{1,2}\s*日游",
    r"去哪玩", r"去哪儿玩", r"怎么玩", r"玩什么",
    r"景点", r"游玩", r"打卡",
    r"路线规划", r"规划.*(行程|路线)", r"安排.*(行程|路线)",
    r"住宿推荐", r"住哪", r"酒店推荐",
    r"必去", r"一日游", r"两日游", r"三日游",
)

# 「城市 + 天数」是旅游的强组合信号（"福州2天"）。
# 必须排除「近 3 天 / 最近 30 天 / 过去 7 天」这类业务时间窗 —— 否则
# 「福州的近3天订单量」会被抢到旅游域。
# 注意 lookbehind 必须同时排除数字：只写 (?<!近) 时，"最近30天" 会在
# 第二位数字处匹配出 "0天"（数字串被截断），断言形同虚设。
_RE_DAY_COUNT = re.compile(r"(?<![\d近])(?<!过去)(?<!前)\d{1,2}\s*[天日](?!气)")


def travel_signal_hits(query: str) -> int:
    """命中的旅游强信号词数量（纯函数；弱命中追问复用，不新增抽取）。"""
    return sum(1 for p in _TRAVEL_PATTERNS if re.search(p, query))


def travel_has_city(query: str) -> bool:
    """query 是否提到种子城市（纯函数）。"""
    return any(city in query for city in poi_seed.all_cities())


def is_travel_request(query: str) -> bool:
    """是否为旅游规划请求（纯函数，可单测）。"""
    if not query:
        return False

    from backend.config.travel import TRAVEL_DETECT_MIN_HITS

    hits = travel_signal_hits(query)
    if hits >= TRAVEL_DETECT_MIN_HITS:
        return True

    if not travel_has_city(query):
        return False

    return hits >= 1 or bool(_RE_DAY_COUNT.search(query))


def try_travel_prefilter(query: str, state: dict) -> dict | None:
    """旅游域预过滤。

    Returns:
        命中旅游域 → 返回主图 state 更新 dict（route_mode="travel"）；
        未命中 / 域关闭 / 判定异常 → None（继续走主 Router）。
    """
    try:
        from backend.config.travel import TRAVEL_ENABLED
        if not TRAVEL_ENABLED:
            return None
    except Exception:
        return None

    if not is_travel_request(query):
        return None

    logger.info("[TravelPrefilter] 旅游域命中: query=%s...", query[:60])

    # 改写为域图可读的初始上下文；目的地由域图 slot_filler 负责抽取，
    # 预过滤不越权做抽取（两处抽取必然分叉）
    return {
        "route_decision": None,
        "route_mode": "travel",
        "travel_context": {
            "conversation_id": state.get("session_id", ""),
            "travel_route": {"source": "prefilter"},
        },
    }
