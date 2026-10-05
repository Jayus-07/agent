"""routing — router_node 职责拆分子包（P1-2，2026-09-30 主架构改造）

从 router_node.py（892 行，四块职责混居）纯移动拆出，对外符号与行为
完全不变（router_node.py 原地 re-export 全部旧符号）：

  prefilter_chain.py   决策组装（_with_router_decisions）+ 域 prefilter 链
  lock_domain.py       客服窗口锁域判定 + redirect_main 转出
  continuation.py      跨轮续跑（ContinuationResolver）+ 旅游 pending
  cs_understanding.py  CS 理解增强（CSUnderstanding 并入 cs_route）
  hierarchical.py      分层路由决策分派（方案四模块外新增，
                       router_node 后续增长块拆分时必须有着落）

依赖方向（冻结，禁止回环）：
  hierarchical → prefilter_chain / cs_understanding；
  prefilter_chain / lock_domain / continuation / cs_understanding 互不依赖；
  router_node 主函数消费全部子模块，子模块不 import router_node。
"""
from backend.orchestration.graph.routing.continuation import (
    _try_continuation,
    try_booking_pending,
    try_travel_pending,
)
from backend.orchestration.graph.routing.cs_understanding import (
    _enrich_with_understanding,
)
from backend.orchestration.graph.routing.hierarchical import (
    _handle_hierarchical_meta,
    _hierarchical_state_fields,
)
from backend.orchestration.graph.routing.lock_domain import (
    CS_FORCED_HINTS,
    detect_cs_redirect,
    is_cs_forced,
)
from backend.orchestration.graph.routing.prefilter_chain import (
    _ROUTE_MODE_DOMAIN,
    _mark_route_from_update,
    _try_cs_prefilter,
    _try_general_chat,
    _with_router_decisions,
    cs_rule_hits_of,
    domain_entry_mode,
    entry_mode_verdict,
    handoff_update_for,
    run_domain_prefilters,
)

__all__ = [
    # prefilter_chain
    "_ROUTE_MODE_DOMAIN",
    "_mark_route_from_update",
    "_with_router_decisions",
    "_try_general_chat",
    "_try_cs_prefilter",
    "cs_rule_hits_of",
    "run_domain_prefilters",
    # 域入口模式（多域隔离 M2）
    "domain_entry_mode",
    "entry_mode_verdict",
    "handoff_update_for",
    # lock_domain
    "CS_FORCED_HINTS",
    "is_cs_forced",
    "detect_cs_redirect",
    # continuation
    "_try_continuation",
    "try_travel_pending",
    "try_booking_pending",
    # cs_understanding
    "_enrich_with_understanding",
    # hierarchical
    "_hierarchical_state_fields",
    "_handle_hierarchical_meta",
]
