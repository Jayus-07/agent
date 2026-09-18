"""entities.py — 实体提取器（规则层，纯函数）。

零改写原则：value 一律从规范化文本原样截取；仅订单号额外提供
match_value（大小写归一），形近错别字（DEM0-1006）原样保留，绝不纠正。
规范化（NFKC 全角→半角）发生在事实源 normalize_query，本模块不做二次变换。
"""
from __future__ import annotations

import re

from backend.customer_service.understanding.types import EntitySpan, EntityType

# 订单号：字母数字混合前缀 + 至少一段连字数字字母段，且整串必须含字母
# （排除 2026-09-15 日期、138-0000 电话分段等纯数字形态）。
# 形近错别字（DEM0-1006 / DEMO-1O08）必须整体命中——零改写的前提是先能
# 原样认领。与 experts/action.py、query.py 的 P3.5 正则同族，接线时收敛。
_ORDER_ID = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Za-z])"
    r"([A-Za-z0-9]{2,10}(?:-[A-Za-z0-9]{2,12})+)(?![A-Za-z0-9])"
)

# 快递单号：常见承运商前缀 + 10~15 位数字
_TRACKING = re.compile(r"(?<![A-Za-z0-9])((?:SF|YT|JD|EMS|ZTO|STO|YUNDA)[A-Za-z]{0,4}\d{10,15})(?![A-Za-z0-9])")
# 无前缀快递单号：快递/运单/物流语境下的 12~15 位纯数字
_TRACKING_BARE = re.compile(r"(?<=[^0-9])(\d{12,15})(?![0-9])")
_TRACKING_CONTEXT = re.compile(r"(快递|运单|物流单|物流号|快递单|tracking)")

# 金额：数字 + 货币标记（含「块」）
_AMOUNT = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(元|块钱|块|RMB|￥|¥)")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![0-9])(1[3-9]\d{9})(?![0-9])")

# 商品：书名号/引号包裹的名称（保守抽取，避免把普通名词当商品）
_PRODUCT = re.compile(r"[「“《]([^」”》]{2,24})[」”》]")


def _span(etype: EntityType, value: str, m: re.Match, group: int = 1,
          match_value: str | None = None) -> EntitySpan:
    return EntitySpan(type=etype, value=value, start=m.start(group),
                      end=m.end(group), match_value=match_value)


def extract_entities(normalized_text: str) -> list[EntitySpan]:
    """从规范化文本抽取实体。顺序：订单号 → 物流号 → 金额 → 邮箱 → 手机号 → 商品。

    纯数字物流号仅在出现物流语境词时才认领，避免吞掉订单号尾段之外的普通数字。
    """
    text = normalized_text or ""
    out: list[EntitySpan] = []

    for m in _ORDER_ID.finditer(text):
        out.append(_span(EntityType.ORDER_ID, m.group(1), m,
                         match_value=m.group(1).upper()))

    for m in _TRACKING.finditer(text):
        out.append(_span(EntityType.TRACKING_NO, m.group(1), m))

    has_log_ctx = bool(_TRACKING_CONTEXT.search(text))
    if has_log_ctx:
        taken = {(s.start, s.end) for s in out}
        for m in _TRACKING_BARE.finditer(text):
            if (m.start(1), m.end(1)) in taken:
                continue
            # 订单号尾段数字不重复认领（订单号 match 区间已占位）
            if any(s.start <= m.start(1) < s.end for s in out):
                continue
            out.append(_span(EntityType.TRACKING_NO, m.group(1), m))

    for m in _AMOUNT.finditer(text):
        # 实体载荷 = 数字部分；货币标记只用于认领，不入 value
        out.append(_span(EntityType.AMOUNT, m.group(1), m))

    for m in _EMAIL.finditer(text):
        out.append(_span(EntityType.EMAIL, m.group(0), m, group=0))

    for m in _PHONE.finditer(text):
        out.append(_span(EntityType.PHONE, m.group(1), m))

    for m in _PRODUCT.finditer(text):
        out.append(_span(EntityType.PRODUCT, m.group(1), m, group=1))

    return out
