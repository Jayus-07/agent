"""travel_v2/models/booking.py — 预订确认门 REST 请求模型

收编重构（2026-11-01）：原 travel_booking 聊天子图壳删除，预订对外形态改为
travel_v2 REST 三段端点（quote → confirm → status）。请求模型全部严格校验
（extra=forbid），search_params 由收编件 request 模型二次确定性校验。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class BookingQuoteRequest(BaseModel):
    """预订报价请求（Offer 定位：确定性重搜后按序号或指纹选中）。"""

    model_config = ConfigDict(extra="forbid")

    commerce_type: Literal["hotel", "flight"]
    # 确定性搜索参数（结构由收编件 HotelSearchRequest/FlightSearchRequest
    # 二次校验——本层不重复写校验规则，单一事实源在收编件）
    search_params: dict[str, Any]
    # {"index": n}（1 起，价格升序）或 {"offer_fingerprint": "..."}
    selection: dict[str, Any]
