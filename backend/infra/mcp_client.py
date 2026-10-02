"""infra.mcp_client — 外部 MCP server 的同步薄客户端（Tool 数据源方向）

2026-10-02 拍板的新方向：外部 MCP server 作为 Tool 的数据源。本模块是
唯一的接入点，消费方（tools/…）只面对一个同步函数 :func:`call_tool`，
不接触 MCP 协议细节。

设计约束：

1. **同步桥接**。LangChain Tool 是同步函数，而官方 mcp SDK 是 asyncio
   生态。此处每次调用开一个短命事件循环 + 短命会话（initialize →
   call_tool → 关闭）。代价是每次多一轮握手 RTT——对本机容器内毫秒级
   往返的低频查询可接受；**不要**在高频路径上用它，届时应改长驻会话。
2. **失败必须显式**。连接失败 / 协议错误 / 工具报错 / 超时统一映射为
   :class:`McpClientError`，由调用方转成「查不了」封套——绝不能把
   异常吞成空结果，否则限流会被当成「没有车票」。
3. **上游无 SLA**（12306 非官方聚合），调用方必须自带降级路径与开关
   （config/mcp.py 的 TRAIN_MCP_*）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import OrderedDict
from datetime import timedelta
from typing import Any

from backend.config import mcp as MCP_CFG
from backend.shared.logger import logger


class McpClientError(Exception):
    """外部 MCP server 调用失败（连接 / 协议 / 工具报错 / 超时）。"""


# ── 节流 + TTL 缓存：外部源怕突发，同参短窗内直接复用 ──
# 节流按 base_url 分桶（12306 与知乎官方接口的限流策略不同，互不拖累）
_throttle_lock = threading.Lock()
_last_call_at: dict[str, float] = {}
_cache: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
_cache_lock = threading.Lock()


def _throttle(key: str, min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _throttle_lock:
        wait = min_interval - (time.time() - _last_call_at.get(key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call_at[key] = time.time()


def _cache_key(base_url: str, tool_name: str, arguments: dict) -> str:
    return f"{base_url}|{tool_name}|{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return None
        expire_at, value = item
        if expire_at < time.time():
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return value


def _cache_put(key: str, value: Any, ttl: float) -> None:
    if ttl <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
        _cache.move_to_end(key)
        while len(_cache) > MCP_CFG.TRAIN_MCP_CACHE_MAXSIZE:
            _cache.popitem(last=False)


def clear_cache() -> int:
    """清空缓存，返回被清除条目数（测试与运维用）。"""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n


def _extract_payload(result) -> Any:
    """把 CallToolResult 归一为 Python 对象。

    优先 structuredContent（协议侧结构化输出），否则拼接 TextContent
    尝试 JSON 解析，再不行透传原文。上游（12306 聚合）两种形态都出现过，
    不能只认其中一种。
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    texts = []
    for block in (getattr(result, "content", None) or []):
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
    joined = "\n".join(texts).strip()
    if not joined:
        return None
    try:
        return json.loads(joined)
    except ValueError:
        return joined


async def _call_async(base_url: str, tool_name: str, arguments: dict,
                      timeout_s: float,
                      headers: dict | None = None) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    timeout = timedelta(seconds=timeout_s)
    async with streamablehttp_client(base_url, timeout=timeout,
                                     headers=headers) as (
        read, write, _
    ):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            init = await asyncio.wait_for(session.initialize(), timeout_s)
            if init is None:
                raise McpClientError(f"MCP 握手失败（无响应）: {base_url}")
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments), timeout_s,
            )
            if getattr(result, "isError", False):
                # 工具侧报错：内容里通常带可读原因，透传给调用方
                payload = _extract_payload(result)
                detail = payload if isinstance(payload, str) else json.dumps(
                    payload, ensure_ascii=False)
                raise McpClientError(f"MCP 工具报错 {tool_name}: {detail}")
            return _extract_payload(result)


def call_tool(base_url: str, tool_name: str, arguments: dict, *,
              timeout: float | None = None,
              ttl: float | None = None,
              headers: dict | None = None,
              min_interval: float | None = None) -> Any:
    """同步调用外部 MCP server 的 tool，返回归一后的 payload。

    Args:
        base_url: MCP 端点，如 ``http://127.0.0.1:18000/mcp``
        tool_name: 上游 tool 名，如 ``query-tickets``
        arguments: 工具参数（dict）
        timeout: 总超时秒数；默认取 ``TRAIN_MCP_TIMEOUT``
        ttl: 结果缓存秒数；默认取 ``TRAIN_MCP_CACHE_TTL``，0 不缓存
        headers: 附加请求头（如知乎官方 MCP 的
            ``{"Authorization": "Bearer <secret>"}``）；默认无
        min_interval: 该源的最小调用间隔秒数（按 base_url 分桶节流）；
            默认取 ``TRAIN_MCP_MIN_INTERVAL``

    Raises:
        McpClientError: 连接失败 / 超时 / 握手失败 / 工具侧报错。
    """
    timeout_s = float(MCP_CFG.TRAIN_MCP_TIMEOUT if timeout is None else timeout)
    effective_ttl = (MCP_CFG.TRAIN_MCP_CACHE_TTL if ttl is None else ttl)
    interval_s = (MCP_CFG.TRAIN_MCP_MIN_INTERVAL if min_interval is None
                  else min_interval)

    ck = _cache_key(base_url, tool_name, arguments)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    _throttle(base_url, interval_s)
    started = time.perf_counter()
    try:
        payload = asyncio.run(_call_async(base_url, tool_name, dict(arguments),
                                          timeout_s, headers))
    except McpClientError:
        raise
    except asyncio.TimeoutError as e:
        raise McpClientError(
            f"MCP 调用超时（>{timeout_s}s）: {base_url}.{tool_name}") from e
    except Exception as e:  # noqa: BLE001 — 连接/协议层异常统一收口
        raise McpClientError(f"MCP 调用失败: {base_url}.{tool_name} — "
                             f"{type(e).__name__}: {e}") from e

    logger.info("[McpClient] %s.%s ok (%.0fms)",
                base_url, tool_name, (time.perf_counter() - started) * 1000)
    _cache_put(ck, payload, effective_ttl)
    return payload
