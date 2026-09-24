"""booking_prefilter.py — Router 内的预订域预过滤（STOP L9）

优先级（L0 §8 定稿）：客服 > 旅游 > 选品 > **预订** > 商务 > 主 Router。
在选品与商务之间加法插入——既有四域两两顺序不变（冻结触碰已登记 L0 §20）：
「订/预订」是交易意图，不得被商务域降级为 search；「找/查」仍是商务。

让路规则：CS 售后（订单退款/退票）由 CS prefilter 先行命中；行程规划信号
（行程/攻略）由旅游域承接——与本文件负向词表构成双保险。
"""
from __future__ import annotations

import re

from backend.shared.logger import logger

_RE_BOOKING = re.compile(
    r"确认预订|预订(?:一下|[一二三两几]?[间张个条])?"
    r"|(?:帮我|给我|我想|我要|打算)?订(?:一间|一张|一个|一下|个)"
    r"|我的预订|预订状态|下单预订")
_RE_CS_SIGNAL = re.compile(r"退款|退票|改签|报销|发票|赔付|投诉")
_RE_TRIP_SIGNAL = re.compile(r"行程|攻略|景点|日游|几天|路线|怎么玩")


def is_booking_request(query: str) -> bool:
    query = (query or "").strip()
    if not query:
        return False
    if _RE_CS_SIGNAL.search(query) or _RE_TRIP_SIGNAL.search(query):
        return False
    return bool(_RE_BOOKING.search(query))


def try_booking_prefilter(query: str, state: dict) -> dict | None:
    try:
        from backend.config.travel_booking import is_booking_enabled

        if not is_booking_enabled():
            return None
    except Exception:
        return None

    if not is_booking_request(query):
        return None

    logger.info("[BookingPrefilter] 预订域命中: query=%s...", query[:60])
    return {
        "route_decision": None,
        "route_mode": "travel_booking",
    }
