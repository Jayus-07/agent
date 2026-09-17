"""providers/travel/booking.py — 预订 Provider 预留（任务书 §12，Phase 7）

**本模块是纯预留：不接线、不调用、不改变任何运行时行为。**

背景（审计结论）：全项目此前无任何预订语义——这是正确的，预订涉及
资金与履约，P0 域图「规划→出单」不需要它。任务书要求 P1 仅落地
**契约形状**，让将来接入真实供应商（门票/酒店/餐饮预约）时：

1. 有统一的 BookingProvider Protocol 可实现（与 POI/Transit 同层）；
2. 状态机与幂等键设计先行定稿，避免各接入方自造口径：
   - **幂等键**是防重复下单的唯一防线：同一 (供应商, POI, 日期, 人数,
     联系人) 重试必须得到同一笔订单，而不是第二张票。生成函数
     ``make_idempotency_key`` 为纯函数，确定性可单测；
   - **状态机**单向流转 pending → confirmed/cancelled/failed/expired，
     不允许 confirmed → pending 这类回跳。
3. 不在此处实现任何真实供应商适配器，也不在域图中调用——接线路径
   （哪个专家触发预订、何时征求用户确认）留给后续专项。

不得在使用前引入真实网络/资金语义：本文件的任何代码被调用即视为误用。
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from backend.providers.travel.facts import now_iso


class BookingStatus(str, Enum):
    """预订状态机（单向流转，不回跳）。"""

    PENDING = "pending"          # 已受理未确认（等待供应商/用户支付）
    CONFIRMED = "confirmed"      # 已确认（出票/预订成功）
    CANCELLED = "cancelled"      # 已取消（用户或供应商发起）
    FAILED = "failed"            # 失败（库存不足/参数被拒等终态）
    EXPIRED = "expired"          # 超时未支付/未确认，自动过期

    @classmethod
    def terminal(cls) -> set["BookingStatus"]:
        """终态集合：离开这些状态只允许显式人工干预。"""
        return {cls.CONFIRMED, cls.CANCELLED, cls.FAILED, cls.EXPIRED}


# 允许的状态流转（from → to 白名单）；不在表内的流转一律拒绝
_ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED,
                            BookingStatus.FAILED, BookingStatus.EXPIRED},
    # 终态不再流转（confirmed → cancelled 走供应商侧语义，不在本层模拟）
    BookingStatus.CONFIRMED: set(),
    BookingStatus.CANCELLED: set(),
    BookingStatus.FAILED: set(),
    BookingStatus.EXPIRED: set(),
}


def can_transition(current: BookingStatus, target: BookingStatus) -> bool:
    """状态流转合法性判定（纯函数，接入方实现 Provider 前的公共护栏）。"""
    return target in _ALLOWED_TRANSITIONS.get(current, set())


def make_idempotency_key(
    provider: str, poi_id: str, visit_date: str,
    party_size: int, contact: str,
) -> str:
    """生成预订幂等键（纯函数，确定性）。

    同一 (供应商, POI, 日期, 人数, 联系人) 的重试/并发请求必须映射到同一键，
    供应商侧据此去重——这是防重复扣款/重复出票的唯一防线。
    键取 sha1 前 16 位：够防碰撞，又不暴露联系人原文（不落敏感信息）。
    """
    blob = "|".join([
        provider.strip().lower(), poi_id.strip(), visit_date.strip(),
        str(int(party_size)), contact.strip(),
    ])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


class BookingRequest(BaseModel):
    """预订请求（供应商无关的最小字段集）。"""

    poi_id: str = Field(..., description="行程 POI 标识，与 Poi.poi_id 对齐")
    visit_date: str = Field(..., description="到访日期 ISO 格式 YYYY-MM-DD")
    party_size: int = Field(..., ge=1, description="人数")
    contact: str = Field(default="", description="联系人标识（脱敏后存储）")
    idempotency_key: str = Field(
        ..., description="幂等键，见 make_idempotency_key——由调用方生成并持有")

    @classmethod
    def create(cls, provider: str, poi_id: str, visit_date: str,
               party_size: int, contact: str = "") -> "BookingRequest":
        """便捷构造：自动派生幂等键（键规则与字段同源，防止两处口径漂移）。"""
        return cls(
            poi_id=poi_id, visit_date=visit_date, party_size=party_size,
            contact=contact,
            idempotency_key=make_idempotency_key(
                provider, poi_id, visit_date, party_size, contact),
        )


class BookingRecord(BaseModel):
    """预订记录（供应商响应的最小落库形状）。"""

    booking_id: str = Field(..., description="供应商侧订单号")
    status: BookingStatus = Field(default=BookingStatus.PENDING)
    request: BookingRequest = Field(..., description="原始请求快照")
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class BookingProvider(Protocol):
    """预订数据源契约（与 POIProvider/TransitProvider 同层，Phase 7 预留）。

    实现方必须：
    - 以 ``idempotency_key`` 做重试去重，同键重试返回同一 BookingRecord；
    - 状态流转经 ``can_transition`` 护栏，不得自造回跳；
    - 任何失败以返回 FAILED 记录表达，不得抛异常穿透到域图。
    """

    name: str

    def create_booking(self, request: BookingRequest) -> BookingRecord:
        """受理预订（幂等：同键重试返回既有记录）。"""
        ...

    def cancel_booking(self, booking_id: str) -> BookingRecord:
        """取消预订（仅 PENDING 可取消，终态拒绝）。"""
        ...

    def get_booking(self, booking_id: str) -> BookingRecord | None:
        """查询预订；不存在返回 None。"""
        ...
