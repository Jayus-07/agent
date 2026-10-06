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

from backend.observability.log_privacy import query_preview
from backend.orchestration.router.projection import route_update_for_mode
from backend.shared.logger import logger
from backend.travel.agents.requirement_agent import _city_name_pattern, _iter_city_hits

# 旅游规划强信号（正则，与 rule_router 的写法保持一致）
_TRAVEL_PATTERNS: tuple[str, ...] = (
    r"行程", r"攻略", r"旅游", r"旅行", r"自由行", r"自驾游", r"出游",
    r"\d{1,2}\s*天\s*\d{0,2}\s*晚", r"\d{1,2}\s*日游",
    r"去哪玩", r"去哪儿玩", r"怎么玩", r"玩什么",
    r"景点", r"游玩", r"打卡",
    r"路线规划", r"规划.*(行程|路线)", r"安排.*(行程|路线)",
    r"住宿推荐", r"住哪", r"酒店推荐",
    r"必去", r"一日游", r"两日游", r"三日游",
    # v3 P0-A 问答信号（丽江好玩吗/上海值得去吗）：域图 intent 层会判成
    # 问答出口，不会误启动规划链——prefilter 只负责「别漏掉旅游话题」
    r"好玩", r"值得去", r"值得玩",
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
    """query 是否提到已知城市（识别名录 ∪ 种子池；纯函数）。

    v3 P0-A：从「种子 3 城」扩为识别名录——名录是识别词表不是支持范围
    闸门（支持 = live Provider 能力边界），「丽江好玩吗/西安三日游」
    这类非种子城市输入不再漏回主路由。
    """
    return bool(_iter_city_hits(query))


def is_travel_request(query: str) -> bool:
    """是否为旅游规划请求（纯函数，可单测）。

    P2 城市解耦（2026-09-22）：域识别与「城市是否支持」分离 ——
    非种子城市的「旅游名词 + 天数」组合（如「纽约3天行程」）仍是强
    旅游信号，先进旅游域，由域内 slot_filler 的 unsupported-city 逻辑
    明确告知「暂不支持该城市」，禁止漏回主 Router 伪装成 RAG 拒答。
    """
    if not query:
        return False

    from backend.config.travel import TRAVEL_DETECT_MIN_HITS

    hits = travel_signal_hits(query)
    if hits >= TRAVEL_DETECT_MIN_HITS:
        return True

    if travel_has_city(query):
        # 无天数的完整城市规划请求也应进域补槽；城市后的业务宾语不命中。
        for pos, city in _iter_city_hits(query):
            match = re.compile(_city_name_pattern(city), re.IGNORECASE).match(query, pos)
            tail = query[match.end():].strip() if match else query
            if re.search(r"(?:规划|安排)(?:一下)?\s*$", query[:pos]):
                if not tail or tail[0] in "，,。！!？?":
                    return True
            # 中文天数+显式动作也可直达，不能用裸“直接规划”抢业务请求。
            if re.fullmatch(r"[一二两三四五六七八九十]{1,3}\s*天[，,\s]*直接规划[。！!？?]*", tail):
                return True
        return hits >= 1 or bool(_RE_DAY_COUNT.search(query))

    # 无种子城市：旅游名词与天数同时在场才判旅游（防「近3天订单量」），
    # 单名词无天数仍走主路由（与旧行为一致）
    return hits >= 1 and bool(_RE_DAY_COUNT.search(query))


def try_travel_prefilter(query: str, state: dict) -> dict | None:
    """旅游域预过滤。

    Returns:
        命中旅游域 → 返回主图 state 更新 dict（route_mode="travel"）；
        未命中 / 域关闭 / 判定异常 → None（继续走主 Router）。
    """
    try:
        # M16：域开关迁 sys_config——DB 覆盖免重启生效，env 作默认值
        from backend.services import sys_config
        if sys_config.get_mode("TRAVEL_ENABLED") != "true":
            return None
    except Exception:
        return None

    if not is_travel_request(query):
        return None

    logger.info("[TravelPrefilter] 旅游域命中: query=%s", query_preview(query))

    # 改写为域图可读的初始上下文；目的地由域图 slot_filler 负责抽取，
    # 预过滤不越权做抽取（两处抽取必然分叉）
    return route_update_for_mode(
        "travel",
        extra={
            "route_decision": None,
            "travel_context": {
                "conversation_id": state.get("session_id", ""),
                "travel_route": {"source": "prefilter"},
            },
        },
    )
