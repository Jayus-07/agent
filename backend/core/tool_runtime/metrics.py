"""tool_runtime/metrics.py — Tool 治理 Prometheus 指标（桥接 observability.metrics）

Label 基数控制：只含 tool / domain / status（status 取 ToolStatus 值），
禁止 user_id / request_id / session_id / error_message 进 label。
所有上报 soft-fail：指标失败不影响业务执行。

P1-4（2026-09-30）：record_tool_result 额外旁路向 Redis 日键 HINCRBY 写
多副本合计（TOOL_STATS_REDIS_ENABLED 默认关，开启时 fire-and-forget——
小线程池异步投递，Redis 不可用静默跳过，绝不阻塞工具热路径）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from backend.config import TOOL_STATS_REDIS_ENABLED
from backend.core.tool_runtime.models import ToolResult, ToolStatus

# 统计投递专用小池：与业务线程池隔离，排队只影响统计延迟不影响请求
_STATS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tool-stats")

# Redis 日键 TTL（秒）：8 天覆盖「今天 + 最近 7 天」的查看窗口
_TOOL_STATS_TTL_SECONDS = 8 * 86400

# 非生产键前缀：治理指标只记生产 Tool 调用。评测探针/测试流量持有的
# tool_key 是一次性 capability 名（如 probe.{uuid}），记入会永久污染
# 「运行时观测到的 Tool」口径（且 lock 永远匹配不上），在唯一入口丢弃。
_NON_PRODUCT_KEY_PREFIXES = ("probe.", "test.")


def _metric_tool_name(result: ToolResult, tool_name: str) -> str:
    """指标/日键统一取 Tool 契约名（@tool 函数名 = lock 键）。

    tool_name 为空时回退 result.tool_name（capability 名）——兼容未传
    tool_name 的调用方（探针、历史代码路径），口径逐调用方收敛。
    """
    return tool_name or result.tool_name


def _write_tool_stats_redis(result: ToolResult, metric_tool: str) -> None:
    """P1-4 写侧同步体：HINCRBY 日键 {prefix}tool_stats:{YYYYMMDD}。

    field = {tool}:total / :ok / :{七分类错误码}；日期进 key + TTL，
    天然滚动分区无需清理任务。任何异常静默（调用方已在异步线程）。
    """
    try:
        from datetime import date

        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is None:
            return
        from backend.observability.error_taxonomy import SUCCESS, unify_tool_status

        error_class = unify_tool_status(result.status)
        key = f"{REDIS_KEY_PREFIX or 'agent:'}tool_stats:{date.today():%Y%m%d}"
        tool = metric_tool
        pipe = r.pipeline()
        pipe.hincrby(key, f"{tool}:total", 1)
        if result.status is ToolStatus.SUCCESS:
            pipe.hincrby(key, f"{tool}:ok", 1)
        elif error_class and error_class != SUCCESS:
            pipe.hincrby(key, f"{tool}:{error_class}", 1)
        pipe.expire(key, _TOOL_STATS_TTL_SECONDS)
        pipe.execute()
    except Exception:  # pragma: no cover — 统计旁路软失败
        pass


def _dispatch_tool_stats_redis(result: ToolResult, metric_tool: str) -> None:
    """异步投递（submit 非阻塞）；开关关/池异常零副作用。"""
    if not TOOL_STATS_REDIS_ENABLED:
        return
    try:
        _STATS_POOL.submit(_write_tool_stats_redis, result, metric_tool)
    except Exception:  # pragma: no cover — 池已关闭等边界
        pass


def _record(metric_name: str, labels: dict, value: float = 1) -> None:
    try:
        from backend.observability import metrics as m
        counter = getattr(m, metric_name, None)
        if counter is None:
            return
        counter.labels(**labels).inc(value)
    except Exception:  # pragma: no cover — 指标软失败
        pass


def _record_unified_error_class(result: ToolResult, domain: str, metric_tool: str) -> None:
    """失败结果按统一七分类计数（M3），分类失败静默不影响主路径。"""
    try:
        from backend.observability.error_taxonomy import SUCCESS, unify_tool_status

        error_class = unify_tool_status(result.status)
        if error_class != SUCCESS:
            _record("agent_tool_error_class_total",
                    {"tool": metric_tool, "domain": domain, "error_class": error_class})
    except Exception:  # pragma: no cover — 分类失败不影响指标主路径
        pass


def record_tool_result(result: ToolResult, domain: str, tool_name: str = "") -> None:
    """Tool 调用结果统一记账（Prometheus + Redis 日键）。

    tool_name：@tool 函数名（契约 lock 键）。治理层执行时由调用方传入；
    主链路历史把 capability 名（如 sql.query）当指标键，导致管理端
    /tools 按 lock 函数名合并时永远匹配不上（行全 0）——本参数修正口径，
    空值回退 result.tool_name（capability）保持向后兼容。
    """
    if result.tool_name.startswith(_NON_PRODUCT_KEY_PREFIXES):
        return
    metric_tool = _metric_tool_name(result, tool_name)
    labels = {"tool": metric_tool, "domain": domain, "status": result.status.value}
    _record("agent_tool_calls_total", labels)
    # P1-4 多副本合计写侧（默认关；开启时异步投递，不阻塞本函数）
    _dispatch_tool_stats_redis(result, metric_tool)
    if result.status is not ToolStatus.SUCCESS:
        _record_unified_error_class(result, domain, metric_tool)
    if result.status is ToolStatus.TIMEOUT:
        _record("agent_tool_timeout_total", {"tool": metric_tool, "domain": domain})
    elif result.status is ToolStatus.RATE_LIMITED or result.status is ToolStatus.FAILED or result.status is ToolStatus.UNAVAILABLE:
        _record("agent_tool_failure_total", {"tool": metric_tool, "domain": domain})
    if result.retry_count:
        _record("agent_tool_retry_total", {"tool": metric_tool, "domain": domain},
                value=result.retry_count)
    if result.fallback_used:
        _record("agent_tool_fallback_total",
                {"tool": metric_tool, "domain": domain, "reason": result.fallback_used})
    # 延迟直方图
    try:
        from backend.observability import metrics as m
        hist = getattr(m, "agent_tool_latency_seconds", None)
        if hist is not None:
            hist.labels(tool=metric_tool).observe(min(result.latency_ms, 120_000) / 1000)
    except Exception:  # pragma: no cover
        pass


def record_circuit_open(tool: str) -> None:
    _record("agent_tool_circuit_open_total", {"tool": tool})


def record_request_degraded(domain: str = "workflow") -> None:
    _record("agent_request_degraded_total", {"domain": domain})
