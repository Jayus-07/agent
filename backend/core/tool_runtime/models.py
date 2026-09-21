"""tool_runtime/models.py — 统一 ToolResult 模型

Domain / LangGraph 只判断 ToolResult.status，不判断底层 Exception
（httpx.TimeoutException / ConnectError / SQLAlchemyError / HTTP 429/5xx /
Python TimeoutError 等由 error_mapper 统一归一化到此）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ToolStatus(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"          # 部分能力降级后仍给出可用结果（如 rerank 失败用未重排 TopK）
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"    # 连接失败 / 服务 DOWN / 熔断 OPEN / 隔离舱满
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    FAILED = "failed"              # 上游业务错误（500 / 校验失败等，重试无意义）


class ToolCriticality(str, Enum):
    """Tool 重要程度（§8）：
    required  失败 → 当前业务任务安全终止，Reporter 给明确不可用说明；
    important 失败 → 允许降级（走降级链/降级回答），流程继续；
    optional  失败 → 直接跳过，一个可选 Tool 失败不能导致整个 Workflow error。
    """
    REQUIRED = "required"
    IMPORTANT = "important"
    OPTIONAL = "optional"


class OperationType(str, Enum):
    READ = "read"
    WRITE = "write"   # 禁止 timeout 后盲目重试（结果可能已生效），必须查操作状态


@dataclass
class ToolResult:
    """统一 Tool 执行结果。status 是唯一需要上层判断的字段。"""

    status: ToolStatus
    tool_name: str
    latency_ms: int = 0

    data: object = None
    error_code: str | None = None
    error_message: str | None = None

    retryable: bool = False
    retry_count: int = 0
    degraded: bool = False

    # 走了哪条降级路径：circuit_breaker / bulkhead / deadline_budget /
    # optional_tool_skipped / check_operation_status ...
    fallback_used: str | None = None
    # ErrorMapper 保留的原始异常（供日志 / trace 使用，禁止透出给用户）
    original_exception: BaseException | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status in (ToolStatus.SUCCESS, ToolStatus.DEGRADED)

    def user_friendly_message(self) -> str:
        """给用户看的一句话（禁止带底层异常细节/堆栈）。"""
        if self.status is ToolStatus.SUCCESS:
            return ""
        messages = {
            ToolStatus.TIMEOUT: "服务响应超时",
            ToolStatus.UNAVAILABLE: "服务暂时不可用",
            ToolStatus.RATE_LIMITED: "服务繁忙",
            ToolStatus.INVALID_REQUEST: "请求参数有误",
            ToolStatus.UNAUTHORIZED: "没有访问权限",
            ToolStatus.FAILED: "服务处理失败",
            ToolStatus.DEGRADED: "服务部分降级，结果可能不完整",
        }
        base = messages.get(self.status, "服务暂时不可用")
        if self.error_code == "CIRCUIT_OPEN":
            base = "服务连续异常已熔断，正在快速降级"
        elif self.error_code == "TOOL_BUSY":
            base = "服务当前并发已满"
        elif self.error_code == "DEADLINE_BUDGET_INSUFFICIENT":
            base = "剩余处理时间不足，跳过该步骤"
        if self.status is ToolStatus.TIMEOUT and self.fallback_used == "check_operation_status":
            base += "，操作结果待确认（请勿重复提交）"
        return base
