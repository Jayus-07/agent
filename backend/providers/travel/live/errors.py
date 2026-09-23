"""providers/travel/live/errors.py — Provider 错误分类学（STOP J1 §60）

原则：**对齐既有 taxonomy，不造第二套异常体系**。腾讯侧的底层异常是
``infra.http.tencent_lbs.TencentLbsError``（status 码语义表 110-348），
本层只做「底层异常 → ProviderStatus + ProviderError」的映射，让上层拿到
统一的结局而不是各处 try 五种异常。

映射表（§17 七态全覆盖）：
  TencentLbsError.is_fatal（110/111/112/113/190/199/301/311）→ UNAUTHORIZED
  TencentLbsError.is_quota（120/121/122/123）                → RATE_LIMITED
  TencentLbsError.status ∈ {303, 347}（无结果）              → NOT_FOUND
  TencentLbsError.status == -1（响应非 JSON，schema 失效）    → INVALID_RESPONSE
  CircuitBreakerOpenError                                    → UNAVAILABLE
  TimeoutError / 本层 budget 超时                            → TIMEOUT
  其余                                                       → UNAVAILABLE
"""
from __future__ import annotations

from backend.providers.travel.live.result import ProviderStatus

# 腾讯「无结果」业务码：调用成功、确实没有（唯一的 negative-cache 合法形态）
LBS_NOT_FOUND_STATUS = frozenset({303, 347})


class ProviderError(Exception):
    """Provider 层统一异常（携带 ProviderStatus 供上层分派降级）。"""

    def __init__(self, status: ProviderStatus, message: str = ""):
        super().__init__(message or status.value)
        self.status = status
        self.message = message or status.value


def status_from_lbs_error(exc: Exception) -> ProviderStatus:
    """底层异常 → ProviderStatus（单一映射点，禁止调用方各自 if-else）。"""
    # 延迟 import：避免模块加载即拉起 infra.http
    from backend.infra.circuit_breaker import CircuitBreakerOpenError
    from backend.infra.http.tencent_lbs import TencentLbsError

    if isinstance(exc, CircuitBreakerOpenError):
        return ProviderStatus.UNAVAILABLE
    if isinstance(exc, TimeoutError):
        return ProviderStatus.TIMEOUT
    if isinstance(exc, TencentLbsError):
        if exc.is_fatal:
            return ProviderStatus.UNAUTHORIZED
        if exc.is_quota:
            return ProviderStatus.RATE_LIMITED
        if exc.status in LBS_NOT_FOUND_STATUS:
            return ProviderStatus.NOT_FOUND
        if exc.status < 0:
            # 客户端约定的「响应体不是合法 JSON」（schema 失效）
            return ProviderStatus.INVALID_RESPONSE
        return ProviderStatus.UNAVAILABLE
    return ProviderStatus.UNAVAILABLE
