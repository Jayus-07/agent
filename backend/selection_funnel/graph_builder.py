"""selection_funnel/graph_builder.py — 选品漏斗域图

拓扑：线性漏斗 + 条件短路（漏斗不修复、只筛选，无 supervisor 循环）：

  START → funnel_brief → funnel_pool → funnel_screen → funnel_verify
        → funnel_econ → funnel_rank → funnel_report → END

任何一层把候选淘空（或缺槽位）→ 直接跳 funnel_report 如实收尾，
不给「服务暂时不可用」式的假错误。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from backend.selection_funnel.brief_node import brief_node
from backend.selection_funnel.graph_state import (
    FUNNEL_BRIEF,
    FUNNEL_ECON,
    FUNNEL_POOL,
    FUNNEL_RANK,
    FUNNEL_REPORT,
    FUNNEL_SCREEN,
    FUNNEL_VERIFY,
    STATUS_NEED_INFO,
    SelectionFunnelState,
)
from backend.selection_funnel.reporter import reporter_node
from backend.selection_funnel.stages.economist import econ_node
from backend.selection_funnel.stages.pool_builder import pool_node
from backend.selection_funnel.stages.ranker import rank_node
from backend.selection_funnel.stages.screener import screen_node
from backend.selection_funnel.stages.verifier import verify_node
from backend.shared.logger import logger

_PIPELINE = (FUNNEL_POOL, FUNNEL_SCREEN, FUNNEL_VERIFY, FUNNEL_ECON, FUNNEL_RANK)

_STAGE_KEYS = {
    FUNNEL_BRIEF: "brief", FUNNEL_POOL: "pool", FUNNEL_SCREEN: "screen",
    FUNNEL_VERIFY: "verify", FUNNEL_ECON: "econ", FUNNEL_RANK: "rank",
}


def _timed(stage: str, fn: Callable) -> Callable:
    """节点耗时记录（P1）：耗时写进该节点自己追加的 stage_logs 条目。"""
    def _inner(state: dict) -> dict:
        t0 = time.perf_counter()
        out = fn(state)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        logs = out.get("stage_logs")
        if (isinstance(logs, list) and logs and isinstance(logs[-1], dict)
                and logs[-1].get("stage") == stage):
            logs[-1]["elapsed_ms"] = elapsed_ms
        return out
    return _inner


def _route_after_stage(state: dict) -> str:
    """条件边：need_info / 淘空 → 直接进 reporter；否则沿漏斗下行。"""
    if state.get("status") == STATUS_NEED_INFO:
        return FUNNEL_REPORT
    if state.get("status") == "empty_pool":
        return FUNNEL_REPORT
    return "next"


def build_selection_funnel_graph() -> Any:
    wf = StateGraph(SelectionFunnelState)

    wf.add_node(FUNNEL_BRIEF, _timed(_STAGE_KEYS[FUNNEL_BRIEF], brief_node))
    wf.add_node(FUNNEL_POOL, _timed(_STAGE_KEYS[FUNNEL_POOL], pool_node))
    wf.add_node(FUNNEL_SCREEN, _timed(_STAGE_KEYS[FUNNEL_SCREEN], screen_node))
    wf.add_node(FUNNEL_VERIFY, _timed(_STAGE_KEYS[FUNNEL_VERIFY], verify_node))
    wf.add_node(FUNNEL_ECON, _timed(_STAGE_KEYS[FUNNEL_ECON], econ_node))
    wf.add_node(FUNNEL_RANK, _timed(_STAGE_KEYS[FUNNEL_RANK], rank_node))
    wf.add_node(FUNNEL_REPORT, reporter_node)

    wf.add_edge(START, FUNNEL_BRIEF)
    wf.add_conditional_edges(
        FUNNEL_BRIEF, _route_after_stage,
        {"next": FUNNEL_POOL, FUNNEL_REPORT: FUNNEL_REPORT})
    prev = FUNNEL_POOL
    for stage in _PIPELINE[1:]:
        wf.add_conditional_edges(
            prev, _route_after_stage, {"next": stage, FUNNEL_REPORT: FUNNEL_REPORT})
        prev = stage
    wf.add_edge(prev, FUNNEL_REPORT)
    wf.add_edge(FUNNEL_REPORT, END)

    graph = wf.compile()
    logger.info("[SelectionFunnelGraph] 编译完成（7 节点，线性漏斗 + 条件短路）")
    return graph


_funnel_graph: Any = None
_funnel_graph_lock = threading.Lock()


def get_selection_funnel_graph() -> Any:
    """获取漏斗域图单例（double-checked locking，与 travel 同策略）。

    P0 不接 checkpointer：漏斗是单轮任务，无跨轮改单诉求；
    接入后 thread_id 纪律见 tests（届时必须逐用例独立 thread_id）。
    """
    global _funnel_graph
    if _funnel_graph is None:
        with _funnel_graph_lock:
            if _funnel_graph is None:
                _funnel_graph = build_selection_funnel_graph()
    return _funnel_graph
