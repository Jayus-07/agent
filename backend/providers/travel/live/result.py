"""providers/travel/live/result.py — Provider 统一返回容器（STOP J1 §16/§17）

任务书红线：**不得把「Provider 挂了」统一成 None**——上层必须能区分
「真的没有（NOT_FOUND）」和「Provider 不可用（UNAVAILABLE/TIMEOUT/...）」，
否则会把限流误当成「这个地方没东西」。

freshness 五态（§33，保持 STOP I 的 unknown != false / estimated != factual）：
  live       本次真实调用取得
  cached     共享缓存命中（未过期）
  stale      stale-if-error：Provider 故障时返回的过期缓存（is_stale=True）
  estimated  本地估算兜底（数据本身带 is_estimate 语义）
  unverified 数据源不提供该事实（如腾讯无营业时间/票价）——由数据字段自带
             verification_status 表达，本容器不重复建模

容器只装 JSON-safe 的归一化数据；腾讯原始 JSON 不允许越过本层（§16）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from backend.providers.travel.facts import now_iso

T = TypeVar("T")


class ProviderStatus(str, Enum):
    """Provider 调用结局（任务书 §17 七态 + 未启用）。"""

    SUCCESS = "success"              # 取到数据
    NOT_FOUND = "not_found"          # 调用成功但确实没有（可 negative cache）
    UNAVAILABLE = "unavailable"      # Provider 不可用/未配置/熔断开路
    RATE_LIMITED = "rate_limited"    # 限流或本方软预算停用
    INVALID_RESPONSE = "invalid_response"  # 响应存在但校验失败（脏数据拒绝）
    TIMEOUT = "timeout"              # 超出本层 timeout budget
    UNAUTHORIZED = "unauthorized"    # 鉴权类失败（重试无意义）
    DISABLED = "disabled"            # 本 Provider 被配置关闭（非故障）


class Freshness(str, Enum):
    """数据新鲜度（缓存层填写；estimated/unverified 由数据字段自带）。"""

    LIVE = "live"
    CACHED = "cached"
    STALE = "stale"
    UNKNOWN = "unknown"  # 无数据时的新鲜度占位（SUCCESS 之外没有意义）


@dataclass
class ProviderResult(Generic[T]):
    """一次 Provider 调用的完整结局。

    data 只有 status==SUCCESS 时非 None；失败时 status + error 说明原因，
    上层据此选择降级路径（§59：fallback 不跨语义）。
    """

    status: ProviderStatus
    data: T | None = None
    provider: str = ""              # provider 标识（如 "tencent:lbs"）
    operation: str = ""             # 能力名（如 "maps.place_search"）
    source_id: str | None = None    # 上游对象 id（POI id 等），可追溯
    observed_at: str = field(default_factory=now_iso)
    error: str = ""                 # 失败时的可读原因（不含 secret/原始响应）
    freshness: Freshness = Freshness.LIVE
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == ProviderStatus.SUCCESS

    def with_freshness(self, freshness: Freshness, observed_at: str) -> "ProviderResult[T]":
        """缓存层标注新鲜度（返回新对象，不改原结果）。"""
        return ProviderResult(
            status=self.status, data=self.data, provider=self.provider,
            operation=self.operation, source_id=self.source_id,
            observed_at=observed_at, error=self.error,
            freshness=freshness, latency_ms=self.latency_ms,
        )


def success(data: T, *, provider: str, operation: str,
            source_id: str | None = None, latency_ms: int = 0) -> ProviderResult[T]:
    return ProviderResult(
        status=ProviderStatus.SUCCESS, data=data, provider=provider,
        operation=operation, source_id=source_id, latency_ms=latency_ms,
    )


def failure(status: ProviderStatus, *, provider: str, operation: str,
            error: str = "", latency_ms: int = 0) -> ProviderResult:
    return ProviderResult(
        status=status, data=None, provider=provider, operation=operation,
        error=error, latency_ms=latency_ms,
    )
