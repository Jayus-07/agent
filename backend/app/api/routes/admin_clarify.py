"""追问漏斗查询 API（2026-10-03 企业口径）

GET /admin/clarify/stats — 追问漏斗双口径聚合 + 转化率
  - live:      进程内 Prometheus 直读（rate 监控口径；进程重启归零，
               附 process_started_at 供前端标注「自重启以来」）
  - persisted: ai.clarify_funnel_events 按时间窗聚合（跨重启精确累计，
               月报口径；clarify_funnel.py 双写）
  - conversion: 以 persisted 为基准的转化率（clicked/shown、resolved/clicked）

权限：管理员。查询范式同 admin_unanswered / admin_security。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query, Request

from backend.app.api.deps import require_admin_user

router = APIRouter(prefix="/admin/clarify", tags=["Admin-Clarify"])

_EVENT_TYPES = ("shown", "clicked", "resolved")


def _query(sql: str, params: tuple) -> list[tuple]:
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _read_live() -> dict:
    """进程内 REGISTRY 直读（M2 admin/tools/stats 同模式），软失败空表。"""
    out: dict[str, dict[str, float]] = {}
    try:
        from prometheus_client import REGISTRY

        for et in _EVENT_TYPES:
            for source, val in _counter_by_source(REGISTRY, et).items():
                out.setdefault(source, {})[et] = val
    except Exception:  # noqa: BLE001 — 指标直读软失败
        pass
    return out


def _counter_by_source(registry, event_type: str) -> dict[str, float]:
    metric = {
        "shown": "agent_clarify_shown_total",
        "clicked": "agent_clarify_clicked_total",
        "resolved": "agent_clarify_resolved_total",
    }[event_type]
    out: dict[str, float] = {}
    for metric_family in registry.collect():
        for sample in metric_family.samples:
            # 按 sample 名匹配（family 名不带 _total 后缀，prometheus_client
            # 内部对 Counter 做了名字改写——按 family 过滤会恒空）
            if sample.name != metric:
                continue
            out[str(sample.labels.get("source", ""))] = float(sample.value)
    return out


def _read_persisted(window_h: int) -> dict[str, dict[str, int]]:
    """PG 按窗口聚合（跨重启精确口径；表不可用时软失败空表）。"""
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - window_h * 3600))
    out: dict[str, dict[str, int]] = {}
    try:
        rows = _query(
            "SELECT source, event_type, COUNT(*) FROM ai.clarify_funnel_events "
            "WHERE created_at >= %s GROUP BY source, event_type",
            (cutoff,),
        )
        for source, event_type, cnt in rows:
            out.setdefault(source or "", {})[event_type] = int(cnt)
    except Exception:  # noqa: BLE001 — 明细表读失败降级
        pass
    return out


def _conversion(persisted: dict) -> dict[str, dict[str, float | None]]:
    """转化率（persisted 基准；分母 0 → None，前端显示 —）。"""
    out = {}
    for source, ev in persisted.items():
        shown = ev.get("shown", 0)
        clicked = ev.get("clicked", 0)
        resolved = ev.get("resolved", 0)
        out[source] = {
            "click_rate": round(clicked / shown, 4) if shown else None,
            "resolve_rate": round(resolved / clicked, 4) if clicked else None,
        }
    return out


@router.get("/stats")
async def clarify_stats(
    request: Request,
    window_h: int = Query(24 * 7, ge=1, le=24 * 90, description="持久化口径时间窗（小时）"),
):
    await require_admin_user(request)

    persisted = _read_persisted(window_h)
    return {
        "window_h": window_h,
        "live": _read_live(),
        "process_uptime_seconds": _process_uptime(),
        "persisted": persisted,
        "conversion": _conversion(persisted),
        "_note": "live=进程内 rate 口径（重启归零，配 rate() 消费）；persisted=PG 精确累计（月报口径）",
    }


def _process_uptime() -> float | None:
    """进程存活秒数（/proc；容器内可用，取不到返回 None 由前端不标注）。

    /proc/self/stat 第 22 字段是进程启动时刻（相对系统开机，单位 tick），
    不是存活时长——须用 /proc/uptime 减去它（实测首版直接当 uptime 用，
    实机冒烟暴露 60252s 假值）。
    """
    try:
        import os

        with open("/proc/uptime", encoding="utf-8") as f:
            system_uptime = float(f.read().split()[0])
        with open("/proc/self/stat", encoding="utf-8") as f:
            fields = f.read().split()
        start_ticks = float(fields[21])
        return round(system_uptime - start_ticks / os.sysconf("SC_CLK_TCK"), 1)
    except Exception:  # noqa: BLE001 — Windows/无 /proc 环境
        return None


__all__ = ["router"]
