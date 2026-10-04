"""clarify_funnel.py — 追问漏斗事件双写（2026-10-03 企业口径）

为什么存在：Prometheus Counter 是进程内口径，重启归零。漏斗的实时监控
走指标序列（rate 口径），「月报级精确累计」走本模块旁路落库的 PG 明细
（ai.clarify_funnel_events，迁移 071）。消费方 GET /api/admin/clarify/stats
同时给两段口径并标注 since/窗口。

双写模式（与 unanswered.py 同一口径）：
- 指标入口即增：漏斗反映事件本身，不因 PG 不可用失真；
- PG 明细软失败：写失败降级为只有计数，永不抛错；
- 上下文从 request_context / trace_collector 权威读取（current_request_context）。
"""
from __future__ import annotations

import json

from backend.observability.unanswered import current_request_context
from backend.shared.logger import logger

# 事件类型白名单：与 metrics.py 四序列中的漏斗三序列一一对应
EVENT_TYPES = ("shown", "clicked", "resolved")


def record_funnel_event(
    event_type: str,
    *,
    source: str,
    detail: dict | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
) -> bool:
    """记录一次漏斗事件（指标 + PG 双写；软失败，永不抛错）。

    调用点（互斥路径，不双计）：
      shown    — events._build_clarify_events（图内卡）/ runner guard 分支（L1 卡）
      clicked  — runner consume_clarify_click 命中处
      resolved — runner _funnel_note_resolved（点击轮非拒答收尾）

    身份来源：显式参数优先——runner 的 clicked 在 trace 建立之前、guard
    shown 在 trace 作用域之外，ContextVar 拿不到（实测 2026-10-03 落了
    空 session 行）；未传时从 request_context/trace_collector 兜底读取。
    """
    if event_type not in EVENT_TYPES:
        logger.warning(f"[clarify_funnel] 未知事件类型: {event_type}")
        return False

    try:
        from backend.observability.metrics import (
            agent_clarify_clicked_total,
            agent_clarify_resolved_total,
            agent_clarify_shown_total,
        )

        counter = {
            "shown": agent_clarify_shown_total,
            "clicked": agent_clarify_clicked_total,
            "resolved": agent_clarify_resolved_total,
        }[event_type]
        counter.labels(source=source or "").inc()
    except Exception:  # noqa: BLE001 — 指标旁路不阻断
        pass

    ctx = current_request_context()
    row = {
        "event_type": event_type,
        "source": (source or "")[:64],
        "session_id": ((session_id if session_id is not None
                        else ctx["session_id"]) or "")[:128],
        "trace_id": ((trace_id if trace_id is not None
                      else ctx["trace_id"]) or ""),
        "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
    }
    try:
        return _insert_event(row)
    except Exception as e:  # noqa: BLE001 — 旁路软失败
        logger.warning(f"[clarify_funnel] 事件落库失败（不影响主流程）: {e}")
        return False


def _insert_event(row: dict) -> bool:
    """PG 写入（收口成独立函数：conftest autouse 在测试会话内替换）。"""
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
        conn.cursor().execute(
            """
            INSERT INTO ai.clarify_funnel_events (
                event_type, source, session_id, trace_id, detail
            ) VALUES (%(event_type)s, %(source)s, %(session_id)s,
                      %(trace_id)s, %(detail)s)
            """,
            row,
        )
        conn.commit()
    return True


__all__ = ["EVENT_TYPES", "record_funnel_event"]
