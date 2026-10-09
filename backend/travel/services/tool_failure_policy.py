"""travel/services/tool_failure_policy.py — 旅游域 Tool Failure Policy

冻结契约（2026-10-07 验收任务书 §39）：

    Tool Failure ≠ Workflow Failure
    External Provider Failure ≠ Internal Planning Failure
    Optional Data Missing ≠ Task Impossible
    DEGRADED ≠ SUCCESS；DEGRADED ≠ FAILED；FAILED ≠ BLOCKED

只有「当前用户目标的 Hard Dependency 无法满足」或「系统核心规划能力
本身无法工作」才允许 Workflow 终止。

职责与边界：

  - 依赖级别**动态判定**（resolve_*）：是否阻断取决于用户当前目标，
    禁止把「12306=optional」这类静态配置当挡箭牌。判定依据是 brief
    与本轮消息里的硬约束信号（如「必须找到 X 点前抵达的高铁」）。
  - **checked 执行**（run_checked）：外部数据源失败 → 降级披露继续；
    硬依赖失败 → BLOCKED 终止；内部代码异常 → 原样上抛（属 Internal
    Planning Failure，走 run_expert_safely 的 failed 收口，绝不静默
    DEGRADED 假装成功）。
  - **不造假**：降级只是「不注入未验证的实时数据 + 明确披露」，绝不
    伪造车次/天气/评分填充结果。

复用口径：本模块**不新建 Tool Result 模型**——结果载体就是平台统一的
``core.tool_runtime.models.ToolResult``（新增 degraded_reason /
fallback_provider / blocking / criticality 字段）；错误分类复用
``ToolStatus`` + ``error_mapper``；重试/熔断/限时复用 SafeToolExecutor
（本模块在其上游，只做域内裁决，不做第二次重试——避免双层重试叠加
尾延迟）。
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, TypeVar

from backend.core.tool_runtime.models import ToolCriticality, ToolResult, ToolStatus
from backend.shared.logger import logger
from backend.travel.core.events import (
    emit_travel_event,
    new_tool_call_id,
    run_travel_tool,
)
from backend.travel.services.live_search_service import LiveSearchError

_T = TypeVar("_T")

# 供 trace / metrics / state 记录的 provider 标识（受控词表，非自由文本）
PROVIDER_12306 = "12306"
PROVIDER_AMAP = "amap"
PROVIDER_TENCENT_LBS = "tencent_lbs"
PROVIDER_ZHIHU_MCP = "zhihu_mcp"
PROVIDER_QWEATHER = "qweather"

# ── 硬交通约束判定（STOP G：动态 Hard Dependency）───────────────────────
# 触发形态：「必须找到 10 月 8 日上午 9 点前抵达泉州的高铁，否则不用做
# 方案」——两个信号同时成立：①「必须/一定/务必」+「X点前」抵达诉求；
# ②「否则不…」的终止语义（没有 ② 的「9 点前到」只是到达时间事实，
# 排程用 brief.arrival_time 即可尊重，不依赖实时班次验证）。
_HARD_ARRIVE_RE = re.compile(
    r"(?:必须|一定|务必|得|要).{0,24}?(\d{1,2}\s*[点：:]\s*(?:\d{1,2})?)\s*(?:之)?前.{0,16}?"
    r"(?:到|抵达|到达)"
)
_TERMINAL_STAKE_RE = re.compile(r"否则|不然|做不到就|查不到就|没有就")
# 「X点前」解析：中文/阿拉伯数字时刻 → HH:MM
_TIME_CN_RE = re.compile(
    r"(\d{1,2})\s*[点：:]\s*(?:(\d{1,2})\s*分?)?")


def resolve_transport_dependency(brief, user_message: str = "") -> ToolCriticality:
    """城际交通 Tool（12306）对当前目标的依赖级别（动态判定）。

    - REQUIRED：本轮表达了「必须 X 点前抵达，否则不要方案」的硬约束，
      实时班次无法验证 = 约束无法验证 → 允许 BLOCKED；
    - IMPORTANT（语义 = PREFERRED）：有出发地+日期的常规查询。失败时
      行程照常生成（交通耗时估算走本地直线估算），只是班次不可见；
    - OPTIONAL：无城际交通诉求，不查（调用方短路，本函数兜底返回）。
    """
    text = (user_message or "").strip()
    if _HARD_ARRIVE_RE.search(text) and _TERMINAL_STAKE_RE.search(text):
        return ToolCriticality.REQUIRED
    if getattr(brief, "origin", "").strip() and getattr(brief, "start_date", None):
        return ToolCriticality.IMPORTANT
    return ToolCriticality.OPTIONAL


def hard_arrival_deadline(user_message: str) -> str:
    """解析硬约束里的「X点前」时刻 → HH:MM；解析不出返回空串。"""
    m = _HARD_ARRIVE_RE.search(user_message or "")
    if not m:
        return ""
    t = _TIME_CN_RE.match(m.group(1).replace(" ", ""))
    if not t:
        return ""
    hour = int(t.group(1))
    minute = int(t.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


# ── 用户可读话术（User-safe message，禁止带异常细节/堆栈）───────────────
_USER_SAFE_MESSAGES: dict[str, str] = {
    PROVIDER_12306: "12306 实时班次暂时无法查询",
    PROVIDER_ZHIHU_MCP: "攻略检索暂时不可用",
    PROVIDER_QWEATHER: "天气预报暂时无法获取",
    PROVIDER_TENCENT_LBS: "地图地点检索暂时不可用",
    PROVIDER_AMAP: "高德数据源暂时不可用",
}
_GENERIC_SAFE_MESSAGE = "实时数据源暂时不可用"


def user_safe_message(provider: str) -> str:
    """Provider 失败的用户可读一句话（不含异常类型/堆栈/内部地址）。"""
    return _USER_SAFE_MESSAGES.get(provider or "", _GENERIC_SAFE_MESSAGE)


def realtime_disclosure(provider: str) -> str:
    """降级后给用户的完整披露句（供 notes 与 SSE user_message）。"""
    return f"{user_safe_message(provider)}，已继续生成行程；" \
           "相关实时信息未经验证，出行前请通过官方渠道确认。"


def blocked_disclosure(provider: str, constraint_hint: str = "") -> str:
    """BLOCKED 终止时的用户话术：如实说明为什么不继续。"""
    base = (f"目前无法验证满足该{constraint_hint or '交通'}条件的实时信息，"
            "因此没有继续生成可能不成立的方案。")
    return base


def record_outcome(
    tool: str,
    provider: str,
    result: ToolResult,
    *,
    workflow_continued: bool,
) -> None:
    """把降级/阻断结局写进 Prometheus 与 trace span（观测旁路，软失败）。

    指标扩展现有 agent_tool_* 体系（record_tool_result 已记 calls/failures，
    此处只补工作流结局维度）；标签全部取受控词表，禁高基数。
    """
    try:
        from backend.observability.metrics import (
            travel_tool_blocked_total,
            travel_tool_degraded_total,
        )

        if result.blocking:
            travel_tool_blocked_total.labels(
                tool=tool, provider=provider).inc()
        elif result.status is ToolStatus.DEGRADED or result.degraded_reason:
            travel_tool_degraded_total.labels(
                tool=tool, provider=provider).inc()
    except Exception:  # noqa: BLE001 — 指标旁路不影响业务
        logger.debug("[TravelFailurePolicy] 指标记录失败: %s", tool,
                     exc_info=True)


def run_checked(
    tool: str,
    agent: str,
    fn: Callable[[], _T],
    *,
    provider: str,
    dependency: ToolCriticality,
    task_id: str = "",
    result_summary: Callable[[Any], dict] | None = None,
    constraint_hint: str = "",
) -> tuple[_T | None, ToolResult]:
    """带 Failure Policy 的 Tool 执行（run_travel_tool 的 checked 变体）。

    Returns:
        (业务结果 or None, ToolResult)：
        - 成功     → (fn 返回值, SUCCESS ToolResult)
        - 外部失败 → (None, DEGRADED 或 FAILED+blocking 的 ToolResult)
          DEGRADED：非硬依赖——已发 tool.degraded 披露，业务继续；
          BLOCKED：硬依赖（REQUIRED）且失败——已发 tool.blocked，
          调用方必须停止规划（不得产出假装满足约束的行程）。
        - 内部异常 → 原样上抛（Internal Planning Failure，绝不吞成降级）。

    与 run_travel_tool 的事件契约一致（tool.started/tool.result），新增
    status=degraded|blocked 两个取值与 user_message 字段（用户可读话术，
    无技术细节）。成功路径的 result_summary 语义不变。
    """
    check = _import_check_run()
    check()
    started_at = time.monotonic()
    call_fields = {"tool_call_id": new_tool_call_id()}
    if task_id:
        call_fields["task_id"] = str(task_id)[:64]
    emit_travel_event("tool.started", agent=agent, tool=tool, **call_fields)
    try:
        value = fn()
        check()
    except LiveSearchError as exc:
        duration_ms = round((time.monotonic() - started_at) * 1000)
        return None, _resolve_failure(
            tool, agent, provider, dependency, exc,
            duration_ms=duration_ms, constraint_hint=constraint_hint,
            call_fields=call_fields)
    # 内部异常（含 CancelledError 语义上抛）：不 catch，让
    # run_expert_safely 按「系统自身不能正确规划」收口为 failed。
    duration_ms = round((time.monotonic() - started_at) * 1000)
    fields: dict[str, Any] = {"status": "success", "duration_ms": duration_ms}
    if result_summary is not None:
        try:
            fields.update(result_summary(value))
        except Exception:  # noqa: BLE001 — 摘要失败不改变真实 Tool 结果
            fields["summary_status"] = "unavailable"
    emit_travel_event(
        "tool.result", agent=agent, tool=tool, **call_fields, **fields)
    return value, ToolResult(
        status=ToolStatus.SUCCESS, tool_name=tool, latency_ms=duration_ms,
        data=value, criticality=dependency,
    )


def _resolve_failure(
    tool: str,
    agent: str,
    provider: str,
    dependency: ToolCriticality,
    exc: LiveSearchError,
    *,
    duration_ms: int,
    constraint_hint: str,
    call_fields: dict[str, str] | None = None,
) -> ToolResult:
    """外部数据源失败的域内裁决：DEGRADED 继续 / BLOCKED 终止。"""
    if dependency is ToolCriticality.REQUIRED:
        message = blocked_disclosure(provider, constraint_hint)
        emit_travel_event(
            "tool.result", agent=agent, tool=tool,
            **(call_fields or {}), status="blocked",
            error_type=type(exc).__name__,
            user_message=message, data_status="unavailable",
            duration_ms=duration_ms,
        )
        emit_travel_event(
            "tool.blocked", agent=agent, tool=tool,
            **(call_fields or {}), provider=provider,
            dependency_level=dependency.value, user_message=message,
        )
        result = ToolResult(
            status=ToolStatus.FAILED, tool_name=tool, latency_ms=duration_ms,
            error_code="hard_dependency_unverifiable",
            error_message=str(exc)[:200],
            degraded_reason=f"硬依赖无法验证：{user_safe_message(provider)}",
            blocking=True, criticality=dependency,
            fallback_used="blocked_stop",
        )
        record_outcome(tool, provider, result, workflow_continued=False)
        return result

    message = realtime_disclosure(provider)
    emit_travel_event(
        "tool.result", agent=agent, tool=tool,
        **(call_fields or {}), status="degraded",
        error_type=type(exc).__name__, user_message=message,
        data_status="unavailable", duration_ms=duration_ms,
    )
    emit_travel_event(
        "tool.degraded", agent=agent, tool=tool,
        **(call_fields or {}), provider=provider,
        dependency_level=dependency.value, user_message=message,
    )
    logger.warning("[TravelFailurePolicy] %s（%s）降级继续: %s", tool,
                   provider, exc)
    result = ToolResult(
        status=ToolStatus.DEGRADED, tool_name=tool, latency_ms=duration_ms,
        error_code="provider_degraded",
        error_message=str(exc)[:200],
        degraded_reason=user_safe_message(provider),
        blocking=False, criticality=dependency,
        fallback_used="no_realtime_data",
    )
    record_outcome(tool, provider, result, workflow_continued=True)
    return result


def state_records(
    tool: str,
    provider: str,
    result: ToolResult,
    *,
    note: str = "",
) -> dict[str, list[dict]]:
    """ToolResult 结局 → state 增量（degraded_tools/blocked_tools/tool_failures）。

    三个键都是「跨专家合并」语义：专家节点把返回的三片列表各自并入
    state 现值（与 notes 同款合并纪律）， planning_reset 跨轮清空。
    """
    record = {
        "tool": tool,
        "provider": provider,
        "dependency_level": result.criticality.value if result.criticality else "",
        "duration_ms": result.latency_ms,
    }
    if result.blocking:
        return {
            "degraded_tools": [],
            "blocked_tools": [{
                **record,
                "reason": result.degraded_reason,
                "user_message": blocked_disclosure(provider),
            }],
            "tool_failures": [{
                **record,
                "error_code": result.error_code or "",
                "error_message": (result.error_message or "")[:200],
            }],
        }
    if result.status is ToolStatus.DEGRADED or result.degraded_reason:
        return {
            "degraded_tools": [{
                **record,
                "reason": result.degraded_reason,
                "note": note or realtime_disclosure(provider),
            }],
            "blocked_tools": [],
            "tool_failures": [{
                **record,
                "error_code": result.error_code or "",
                "error_message": (result.error_message or "")[:200],
            }],
        }
    return {"degraded_tools": [], "blocked_tools": [], "tool_failures": []}


def _import_check_run():
    from backend.travel.request_runtime import check_run

    return check_run


__all__ = [
    "PROVIDER_12306",
    "PROVIDER_AMAP",
    "PROVIDER_QWEATHER",
    "PROVIDER_TENCENT_LBS",
    "PROVIDER_ZHIHU_MCP",
    "blocked_disclosure",
    "hard_arrival_deadline",
    "realtime_disclosure",
    "record_outcome",
    "resolve_transport_dependency",
    "run_checked",
    "run_travel_tool",
    "state_records",
    "user_safe_message",
]
