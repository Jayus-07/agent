"""travel/graph_builder.py — 旅游域图构建器

拓扑（与 CS Graph 同样是独立子图 + 自带 reporter）：
  START → travel_slot_filler → travel_supervisor
            ├─ travel_poi_expert      ─┐
            ├─ travel_transit_expert   │
            ├─ travel_budget_expert    ├→ travel_supervisor（循环）
            ├─ travel_risk_expert      │
            ├─ travel_validator       ─┘
            └─ travel_repair ──────────→ travel_supervisor
  travel_supervisor → travel_reporter → END

为什么用「supervisor 单点入、单点出」而不是 Send 并行派发：
P0 的四个专家之间存在**严格的数据依赖**（poi → transit → budget/risk），
真正可并行的只有 budget 与 risk 两个，并行收益有限而调度复杂度上升。
先跑通契约与校验，P1 再把 budget/risk 改为 Send 并行（届时两者只读同一份
itinerary，互不写入，天然可并行）。
"""
from __future__ import annotations

import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.travel.experts.budget import budget_expert_node
from backend.travel.experts.poi import poi_expert_node
from backend.travel.experts.risk import risk_expert_node
from backend.travel.experts.transit import transit_expert_node
from backend.travel.graph_state import (
    TRAVEL_BUDGET_EXPERT,
    TRAVEL_POI_EXPERT,
    TRAVEL_REPAIR,
    TRAVEL_REPORTER,
    TRAVEL_RISK_EXPERT,
    TRAVEL_SLOT_FILLER,
    TRAVEL_SUPERVISOR,
    TRAVEL_TRANSIT_EXPERT,
    TRAVEL_VALIDATOR,
    TravelGraphState,
)
from backend.travel.repair import repair_node
from backend.travel.reporter import travel_reporter_node
from backend.travel.slot_filler import slot_filler_node
from backend.travel.supervisor import travel_supervisor_node
from backend.travel.validator import travel_validator_node
from backend.shared.logger import logger

# 回到调度器的节点（supervisor 是唯一的汇聚点）
_BACK_TO_SUPERVISOR = (
    TRAVEL_POI_EXPERT, TRAVEL_TRANSIT_EXPERT, TRAVEL_BUDGET_EXPERT,
    TRAVEL_RISK_EXPERT, TRAVEL_VALIDATOR, TRAVEL_REPAIR,
)


def build_travel_graph(checkpointer: Any = None) -> Any:
    """构建并编译旅游域图。"""
    wf = StateGraph(TravelGraphState)

    wf.add_node(TRAVEL_SLOT_FILLER, slot_filler_node)
    wf.add_node(TRAVEL_SUPERVISOR, travel_supervisor_node)
    wf.add_node(TRAVEL_POI_EXPERT, poi_expert_node)
    wf.add_node(TRAVEL_TRANSIT_EXPERT, transit_expert_node)
    wf.add_node(TRAVEL_BUDGET_EXPERT, budget_expert_node)
    wf.add_node(TRAVEL_RISK_EXPERT, risk_expert_node)
    wf.add_node(TRAVEL_VALIDATOR, travel_validator_node)
    wf.add_node(TRAVEL_REPAIR, repair_node)
    wf.add_node(TRAVEL_REPORTER, travel_reporter_node)

    wf.add_edge(START, TRAVEL_SLOT_FILLER)
    wf.add_edge(TRAVEL_SLOT_FILLER, TRAVEL_SUPERVISOR)

    for node in _BACK_TO_SUPERVISOR:
        wf.add_edge(node, TRAVEL_SUPERVISOR)

    wf.add_edge(TRAVEL_REPORTER, END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    graph = wf.compile(**compile_kwargs)
    logger.info("[TravelGraph] 编译完成（9 节点，checkpointer=%s）",
                "on" if checkpointer else "off")
    return graph


_travel_graph: Any = None
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
PERSISTENCE_DEGRADED = "degraded"    # 降级到 MemorySaver（进程内存，重启即失）
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


def _build_checkpointer() -> tuple[Any, str]:
    """按 TRAVEL_CHECKPOINTER_ENABLED 构建 checkpointer，**返回 (实例, 状态)**。

    与 CS 域图 / 主图同策略：默认关；**Postgres 优先**（跨进程、重启保留、
    多 worker 共享），MemorySaver 仅作初始化失败与本地调试的降级 —— 内存实现
    在进程内只增不减，且多 worker 各存一份，不能当生产方案。

    状态语义（任务书 §10）：
      healthy   持久后端就绪，跨轮改单可信；
      degraded  postgres 不可用退到 MemorySaver —— 同进程内跨轮仍可用，
                但重启即失、多 worker 不共享，必须全链路披露；
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
        except Exception:
            # 说清后果：不是「没启用」，而是「启用了但不持久」——
            # 状态只在进程内存里，重启即失、多 worker 各存一份。
            # 降级状态由 get_persistence_status() 上浮（任务书 §10），
            # 不再只是日志里的一行。
            logger.warning(
                "[TravelGraph] 配置的后端 postgres 不可用（多为缺 psycopg v3 / "
                "langgraph-checkpoint-postgres），已降级为 MemorySaver："
                "跨轮状态不持久化、多 worker 不共享", exc_info=True)

    try:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info("[TravelGraph] checkpointer enabled (MemorySaver, degraded)")
        return MemorySaver(), PERSISTENCE_DEGRADED
    except Exception:
        logger.warning("[TravelGraph] checkpointer init failed, running without")
        return None, PERSISTENCE_DEGRADED
