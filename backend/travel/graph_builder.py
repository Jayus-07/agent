"""travel/graph_builder.py — 旅游域图构建器

拓扑（与 CS Graph 同样是独立子图 + 自带 reporter）：
  START → travel_slot_filler → travel_supervisor
            ├─ travel_auxiliary_tasks ─┘（本轮附加查询，失败独立降级）
            ├─ travel_poi_expert      ─┐
            ├─ travel_transit_expert   │
            ├─ travel_weather_expert   ├→ travel_supervisor（循环）
            ├─ travel_budget_expert    │
            ├─ travel_risk_expert      │
            ├─ travel_validator       ─┘
            └─ travel_repair ──────────→ travel_supervisor
  travel_supervisor → travel_reporter → END

为什么用「supervisor 单点入、单点出」而不是 Send 并行派发：
P0 的专家之间存在**严格的数据依赖**（poi → transit → weather → budget/risk），
真正可并行的只有 budget 与 risk 两个，并行收益有限而调度复杂度上升。
先跑通契约与校验，后续再把 budget/risk 改为 Send 并行（届时两者只读同一份
itinerary，互不写入，天然可并行）。

weather（2026-09-22）：排程后按出行日期核查天气，坏天气日做户外→室内
替换 —— 位于 transit 后（需要重排当日时刻表）、budget 前（替换会改变
门票/通勤费用，先换再算钱，预算口径才一致）。
"""
from __future__ import annotations

import functools
import threading
import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.shared.logger import logger
from backend.travel.auxiliary_tasks import auxiliary_tasks_node
from backend.travel.experts.budget import budget_expert_node
from backend.travel.experts.poi import poi_expert_node
from backend.travel.experts.risk import risk_expert_node
from backend.travel.experts.transit import transit_expert_node
from backend.travel.experts.weather import weather_expert_node
from backend.travel.graph_state import (
    TRAVEL_AUXILIARY_TASKS,
    TRAVEL_BUDGET_EXPERT,
    TRAVEL_PARTIAL_REPLAN,
    TRAVEL_POI_EXPERT,
    TRAVEL_REPAIR,
    TRAVEL_REPORTER,
    TRAVEL_RISK_EXPERT,
    TRAVEL_SLOT_FILLER,
    TRAVEL_SUPERVISOR,
    TRAVEL_TRANSIT_EXPERT,
    TRAVEL_VALIDATOR,
    TRAVEL_WEATHER_EXPERT,
    TravelGraphState,
)
from backend.travel.partial_replan_node import partial_replan_node
from backend.travel.repair import repair_node
from backend.travel.reporter import travel_reporter_node
from backend.travel.slot_filler import slot_filler_node
from backend.travel.supervisor import travel_supervisor_node
from backend.travel.validator import travel_validator_node


def _evented_node(node_name: str, node_fn):
    """给真实 LangGraph 节点加事件投影，不改变节点输入输出。"""
    @functools.wraps(node_fn)
    def wrapped(state):
        from backend.travel.core.events import emit_travel_event
        from backend.travel.request_runtime import check_run

        def _record_stage(status: str) -> None:
            """Prometheus 阶段记账（2026-10-06 观测重构；软失败不影响图执行）。"""
            try:
                from backend.observability.metrics import (
                    agent_stage_duration_seconds,
                    agent_stage_total,
                )
                elapsed = time.monotonic() - started_at
                agent_stage_duration_seconds.labels(
                    domain="travel", stage=node_name).observe(elapsed)
                agent_stage_total.labels(
                    domain="travel", stage=node_name, status=status).inc()
            except Exception:  # noqa: BLE001 — 指标旁路软失败
                pass

        check_run()
        started_at = time.monotonic()
        emit_travel_event(
            "stage.started", agent="travel_graph", stage=node_name,
        )
        try:
            update = node_fn(state)
            check_run()
        except Exception as exc:  # noqa: BLE001 — 事件后保持节点原异常
            emit_travel_event(
                "stage.finished", agent="travel_graph", stage=node_name,
                status="failed", error_type=type(exc).__name__,
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )
            _record_stage("failed")
            raise

        status = "success"
        error_type = ""
        if isinstance(update, dict):
            expert_result = update.get("last_expert_result") or {}
            if expert_result.get("status") == "failed":
                status = "failed"
                error_type = str(expert_result.get("error") or "ToolFailed")
        emit_travel_event(
            "stage.finished", agent="travel_graph", stage=node_name,
            status=status,
            error_type=error_type,
            duration_ms=round((time.monotonic() - started_at) * 1000),
        )
        _record_stage(status)
        return update

    return wrapped

# 回到调度器的节点（supervisor 是唯一的汇聚点）
_BACK_TO_SUPERVISOR = (
    TRAVEL_POI_EXPERT, TRAVEL_TRANSIT_EXPERT, TRAVEL_WEATHER_EXPERT,
    TRAVEL_BUDGET_EXPERT, TRAVEL_RISK_EXPERT, TRAVEL_VALIDATOR, TRAVEL_REPAIR,
    TRAVEL_PARTIAL_REPLAN,
    TRAVEL_AUXILIARY_TASKS,
)


def build_travel_graph(checkpointer: Any = None) -> Any:
    """构建并编译旅游域图。"""
    wf = StateGraph(TravelGraphState)

    wf.add_node(TRAVEL_SLOT_FILLER, _evented_node(
        TRAVEL_SLOT_FILLER, slot_filler_node))
    wf.add_node(TRAVEL_SUPERVISOR, _evented_node(
        TRAVEL_SUPERVISOR, travel_supervisor_node))
    wf.add_node(TRAVEL_POI_EXPERT, _evented_node(
        TRAVEL_POI_EXPERT, poi_expert_node))
    wf.add_node(TRAVEL_TRANSIT_EXPERT, _evented_node(
        TRAVEL_TRANSIT_EXPERT, transit_expert_node))
    wf.add_node(TRAVEL_WEATHER_EXPERT, _evented_node(
        TRAVEL_WEATHER_EXPERT, weather_expert_node))
    wf.add_node(TRAVEL_BUDGET_EXPERT, _evented_node(
        TRAVEL_BUDGET_EXPERT, budget_expert_node))
    wf.add_node(TRAVEL_RISK_EXPERT, _evented_node(
        TRAVEL_RISK_EXPERT, risk_expert_node))
    wf.add_node(TRAVEL_VALIDATOR, _evented_node(
        TRAVEL_VALIDATOR, travel_validator_node))
    wf.add_node(TRAVEL_REPAIR, _evented_node(
        TRAVEL_REPAIR, repair_node))
    wf.add_node(TRAVEL_PARTIAL_REPLAN, _evented_node(
        TRAVEL_PARTIAL_REPLAN, partial_replan_node))
    wf.add_node(TRAVEL_AUXILIARY_TASKS, _evented_node(
        TRAVEL_AUXILIARY_TASKS, auxiliary_tasks_node))
    wf.add_node(TRAVEL_REPORTER, _evented_node(
        TRAVEL_REPORTER, travel_reporter_node))

    wf.add_edge(START, TRAVEL_SLOT_FILLER)
    wf.add_edge(TRAVEL_SLOT_FILLER, TRAVEL_SUPERVISOR)

    for node in _BACK_TO_SUPERVISOR:
        wf.add_edge(node, TRAVEL_SUPERVISOR)

    wf.add_edge(TRAVEL_REPORTER, END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    graph = wf.compile(**compile_kwargs)
    logger.info("[TravelGraph] 编译完成（12 节点，checkpointer=%s）",
                "on" if checkpointer else "off")
    return graph


_travel_graph: Any = None
_travel_read_only_graph: Any = None
_travel_graph_lock = threading.Lock()

# ============================================================
# 持久化状态可见性（任务书 §10）
# ============================================================
# checkpointer 的三级降级此前只有 logger.warning —— 状态、trace、行程单都
# 不知道持久化已降级，多 worker 部署时「跨轮改单静默失效」无法归因。
# 工厂现在返回 (checkpointer, status)，status 在图构建时落模块级单例，
# 由 slot_filler（图入口）每轮写入 state，沿 supervisor_decision / reporter
# 传播到 trace 与行程单 —— 降级从「日志里一行」变成「全链路可见的事实」。
PERSISTENCE_HEALTHY = "healthy"      # postgres 等持久后端就绪
PERSISTENCE_DEGRADED = "degraded"    # 显式 MemorySaver（进程内存，重启即失）
PERSISTENCE_DISABLED = "disabled"    # 未启用 checkpointer（无跨轮能力）

_persistence_status: str = PERSISTENCE_DISABLED


def get_persistence_status() -> str:
    """当前域图单例的持久化状态（healthy / degraded / disabled）。

    在 get_travel_graph() 首次构建时确定。测试可直接改私有变量或走
    _build_checkpointer() 重算。
    """
    return _persistence_status


def get_travel_graph() -> Any:
    """获取旅游域图单例（double-checked locking）。"""
    global _travel_graph, _persistence_status
    if _travel_graph is None:
        with _travel_graph_lock:
            if _travel_graph is None:
                checkpointer, status = _build_checkpointer()
                _persistence_status = status
                _travel_graph = build_travel_graph(checkpointer=checkpointer)
    return _travel_graph


def get_travel_read_only_graph() -> Any:
    """获取不挂 checkpointer 的问答图，避免问答覆盖行程执行态。"""
    global _travel_read_only_graph
    if _travel_read_only_graph is None:
        with _travel_graph_lock:
            if _travel_read_only_graph is None:
                _travel_read_only_graph = build_travel_graph(checkpointer=None)
    return _travel_read_only_graph


def _build_checkpointer() -> tuple[Any, str]:
    """按 TRAVEL_CHECKPOINTER_ENABLED 构建 checkpointer，**返回 (实例, 状态)**。

    默认关；**Postgres 优先**（跨进程、重启保留、多 worker 共享）。
    Postgres 初始化失败默认 fail-loud，禁止把「已开启持久化」静默变成
    MemorySaver；只有显式 ``CHECKPOINTER_ALLOW_DEGRADE=true`` 才允许降级。
    ``TRAVEL_CHECKPOINTER_BACKEND=memory`` 是测试/本地调试的显式选择。

    状态语义（任务书 §10）：
      healthy   持久后端就绪，跨轮改单可信；
      degraded  显式选择 memory，或显式允许 postgres 失败后退到 MemorySaver；
                同进程内跨轮仍可用，但重启即失、多 worker 不共享；
      disabled  未启用 checkpointer，无跨轮能力（属配置选择，非事故）。

    开启的真实用途是**跨轮改单**：状态里留着上一轮的 slot/brief/itinerary，
    第二轮说「第二天想轻松点」才能在既有骨架上局部重排；否则每轮都从头规划，
    用户会拿到一份与上一轮无关的新行程。
    """
    from backend.config.travel import (
        TRAVEL_CHECKPOINT_TTL_DAYS,
        TRAVEL_CHECKPOINTER_BACKEND,
        TRAVEL_CHECKPOINTER_ENABLED,
    )

    if not TRAVEL_CHECKPOINTER_ENABLED:
        return None, PERSISTENCE_DISABLED

    if TRAVEL_CHECKPOINTER_BACKEND == "postgres":
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver

            from backend.config.database import MEMORY_DB_CONFIG
            c = MEMORY_DB_CONFIG
            dsn = (f"postgresql://{c['user']}:{c['password']}"
                   f"@{c['host']}:{c['port']}/{c['dbname']}")
            # autocommit：checkpointer 写入需即时提交（官方建议）
            conn = psycopg.Connection.connect(dsn, autocommit=True)
            checkpointer = PostgresSaver(conn)
            checkpointer.setup()  # 首次建表（幂等）
            logger.info("[TravelGraph] checkpointer enabled (PostgresSaver: %s/%s)",
                        c["host"], c["dbname"])
            # TTL 清理守护：与主图 / 客服域共用同一组表，全进程单例幂等启动
            try:
                from backend.orchestration.graph.checkpointer_cleanup import (
                    start_cleanup_daemon,
                )
                start_cleanup_daemon(TRAVEL_CHECKPOINT_TTL_DAYS, owner="travel")
            except Exception:
                logger.debug("[TravelGraph] cleanup daemon 启动失败（非致命）",
                             exc_info=True)
            return checkpointer, PERSISTENCE_HEALTHY
        except Exception as exc:
            from backend.config.checkpointer import degrade_allowed

            reason = (
                "PostgresSaver 初始化失败（请检查 psycopg v3、"
                "langgraph-checkpoint-postgres、数据库连接与 setup）"
            )
            if not degrade_allowed():
                # 这是配置为启用持久化却无法提供持久化的启动错误，
                # 不把它伪装成普通 warning，也不偷偷换成进程内存。
                logger.error("[TravelGraph] %s，拒绝静默降级", reason,
                             exc_info=True)
                from backend.config.checkpointer import CheckpointerUnavailable
                raise CheckpointerUnavailable(
                    f"[TravelGraph] {reason}；"
                    "未设置 CHECKPOINTER_ALLOW_DEGRADE=true，已 fail-loud。"
                ) from exc
            logger.warning(
                "[TravelGraph] %s，因 CHECKPOINTER_ALLOW_DEGRADE=true "
                "显式降级为 MemorySaver：跨轮状态不持久化、多 worker 不共享",
                reason, exc_info=True)
            from langgraph.checkpoint.memory import MemorySaver
            return MemorySaver(), PERSISTENCE_DEGRADED

    if TRAVEL_CHECKPOINTER_BACKEND == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        logger.info(
            "[TravelGraph] checkpointer enabled (MemorySaver, explicit degraded)")
        return MemorySaver(), PERSISTENCE_DEGRADED

    raise ValueError(
        f"[TravelGraph] 不支持的 checkpointer backend: "
        f"{TRAVEL_CHECKPOINTER_BACKEND!r}；仅支持 postgres 或显式 memory"
    )
