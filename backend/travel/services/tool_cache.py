"""travel/services/tool_cache.py — 外部 Tool 结果通用缓存层（M3-d）

目的：外部数据源（腾讯 LBS/高德/12306/知乎 MCP）必然抖动——同参数检索在
TTL 内直接复用旧结果，抗抖动 + 省外部 API 配额。命中不改变业务语义：
封套原样返回，仅注入 ``cache_hit: true`` 供观测（调用方可据此标注
「缓存数据」）。

Key 口径：``travel:toolcache:{tool_name}:{tenant}:{sha256(sorted params)}``。
失败封套与异常**不缓存**（保留即时重试语义）。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from backend.shared.logger import logger


def _tenant() -> str:
    """租户维度隔离缓存键（多租户部署下防止跨租户串数据）。"""
    try:
        from backend.tools.session import get_tool_tenant_id

        return get_tool_tenant_id() or "default"
    except Exception:  # noqa: BLE001 — 会话上下文缺失时退默认租户
        return "default"


def _cache_key(tool_name: str, params: dict[str, Any]) -> str:
    raw = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{tool_name}:{_tenant()}:{digest}"


def cached_envelope(
    tool_name: str,
    params: dict[str, Any],
    invoke: Callable[[], str],
    *,
    ttl: int,
) -> tuple[str, bool]:
    """带缓存的 Tool 封套调用。返回 (封套字符串, 是否缓存命中)。

    只缓存成功封套（status=success）；失败/空结果不缓存，保留重试语义。
    """
    from backend.config.travel import TRAVEL_TOOL_CACHE_ENABLED
    from backend.infra.cache.backend import get_cache

    if not TRAVEL_TOOL_CACHE_ENABLED:
        return invoke(), False

    key = _cache_key(tool_name, params)
    try:
        cache = get_cache("travel_tool_cache", ttl=ttl)
        hit = cache.get_json(key)
    except Exception as e:  # noqa: BLE001 — 缓存故障不阻塞主链
        logger.warning("[ToolCache] 读取失败（跳过缓存）: %s", e)
        hit = None

    if isinstance(hit, dict) and hit.get("status") == "success":
        logger.info("[ToolCache] 命中 %s", key)
        return json.dumps(hit, ensure_ascii=False), True

    raw = invoke()
    try:
        envelope = json.loads(raw)
    except Exception:  # noqa: BLE001 — 非 JSON 封套不缓存
        return raw, False
    if isinstance(envelope, dict) and envelope.get("status") == "success":
        try:
            cache.set_json(key, envelope, ttl=ttl)
        except Exception as e:  # noqa: BLE001
            logger.debug("[ToolCache] 写入失败: %s", e)
    return raw, False
