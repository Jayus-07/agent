"""customer_service/policy/refund_policy.py — 退款政策（结构化、带版本）

分工（2026-10-14 拍板，方案 A）：
  - 本模块是退款「窗口天数 / 可退状态 / 政策版本」的唯一事实源，
    候选列表（list_refund_candidates）与资格判断（check_refund_eligibility）
    都读这里，口径不会再分叉。
  - RAG 只负责向用户解释政策并给出处，不参与放行判断。此前窗口天数靠
    正则从 RAG 检索文本里抠，解析失败静默回退 7 天，且只用于候选筛选、
    资格判断根本不用，导致「列表里没有、直接申请却能过」。
  - 配置缺失或非法 → 抛 RefundPolicyUnavailableError，写操作拒绝，
    绝不回退一个「看起来合理」的默认值放行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.customer_service.errors import RefundPolicyUnavailableError

_MIN_WINDOW_DAYS = 1
_MAX_WINDOW_DAYS = 365


@dataclass(frozen=True)
class RefundPolicy:
    version: str
    window_days: int
    refundable_statuses: frozenset[str]

    def is_within_window(
        self, created_at: datetime, now: datetime | None = None,
    ) -> bool:
        """下单时间是否在窗口内（与候选 SQL 的 created_at >= NOW() - N 天同口径）。"""
        now = now or datetime.now(timezone.utc)
        return now - created_at <= timedelta(days=self.window_days)


def get_refund_policy() -> RefundPolicy:
    """读取并校验当前退款政策；任何一项非法即 fail-closed。"""
    # 运行时从 config 模块取值（而非 import 期绑定），便于测试与热改环境变量后重载。
    from backend.config import customer_service as cfg

    version = str(getattr(cfg, "CS_REFUND_POLICY_VERSION", "") or "").strip()
    if not version:
        raise RefundPolicyUnavailableError("退款政策版本号缺失")

    raw_days = str(getattr(cfg, "CS_REFUND_WINDOW_DAYS", "") or "").strip()
    try:
        days = int(raw_days)
    except ValueError:
        raise RefundPolicyUnavailableError(
            f"退款窗口天数非法: {raw_days!r}（policy={version}）"
        ) from None
    if not _MIN_WINDOW_DAYS <= days <= _MAX_WINDOW_DAYS:
        raise RefundPolicyUnavailableError(
            f"退款窗口天数越界: {days}（允许 {_MIN_WINDOW_DAYS}-{_MAX_WINDOW_DAYS}，"
            f"policy={version}）"
        )

    raw_statuses = str(getattr(cfg, "CS_REFUNDABLE_STATUSES", "") or "")
    statuses = frozenset(
        part.strip() for part in raw_statuses.split(",") if part.strip()
    )
    if not statuses:
        raise RefundPolicyUnavailableError(
            f"可退订单状态集合为空（policy={version}）"
        )

    return RefundPolicy(
        version=version, window_days=days, refundable_statuses=statuses,
    )


def parse_order_created_at(value: Any) -> datetime | None:
    """订单 created_at → aware datetime；无法解析返回 None（调用方 fail-closed）。

    兼容三种来源：沙盒库 datetime、http 网关 ISO 字符串（含 Z / 偏移）、
    纯日期字符串。无时区信息按 UTC 处理。
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
