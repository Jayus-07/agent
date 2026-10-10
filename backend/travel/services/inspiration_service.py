"""travel/services/inspiration_service.py — 问答/灵感层的只读检索聚合（v3 P0-A 薄层）

职责：QUERY_STATIC 意图一轮内的**一次定向攻略检索**（backend/travel/core/intent.py：首次一次
定向检索，证据不足的补检是 P0-B 的轮次上限机制，本层固定 1 次不补检）。
复用 live_search_service 的知乎官方 MCP 通路与统一封套解码，本层不做
Provider 级新事物；产出可序列化的灵感包，reporter 只渲染不检索。

检索是**增强信息**：失败/空结果/未启用都以 status 三态如实呈现
（available / empty / unavailable，对应 本模块 的三态文案），绝不阻塞
其他链路，也绝不捏造知乎来源。
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger
from backend.travel.services import live_search_service
from backend.travel.services.live_search_service import LiveSearchError

_SUMMARY_MAX = 120


def _normalize_guide(item: dict[str, Any], source: str) -> dict[str, Any]:
    """知乎归一化结果 → 灵感卡最小字段（只挑展示需要的，不透传大对象）。"""
    summary = str(item.get("summary") or "").strip()
    if len(summary) > _SUMMARY_MAX:
        summary = summary[:_SUMMARY_MAX] + "…"
    guide: dict[str, Any] = {
        "title": str(item.get("title") or "").strip(),
        "summary": summary,
        "source": source,
    }
    for key in ("url", "author_name"):
        if item.get(key):
            guide[key] = str(item[key])
    return guide


def fetch_destination_inspiration(destination: str, *, limit: int = 4) -> dict:
    """目的地灵感包：知乎定向检索一次，三态 status，软失败不阻塞。"""
    destination = (destination or "").strip()
    if not destination:
        return {"destination": "", "guides": [], "status": "empty"}
    try:
        payload = live_search_service.search_zhihu_guides(
            destination=destination, limit=limit)
        guides = [
            g for g in (
                _normalize_guide(item, "zhihu")
                for item in (payload.get("results") or [])
                if isinstance(item, dict)
            ) if g["title"]
        ]
        status = "available" if guides else "empty"
    except LiveSearchError as exc:
        logger.warning("[Inspiration] 攻略检索不可用: %s", exc)
        guides, status = [], "unavailable"
    except Exception:  # noqa: BLE001 — 灵感是增强信息，任何异常都降级为不可用
        logger.warning("[Inspiration] 攻略检索异常降级", exc_info=True)
        guides, status = [], "unavailable"
    return {
        "destination": destination,
        "guides": guides,
        "status": status,
    }


__all__ = ["fetch_destination_inspiration"]
