"""selection_funnel/register.py — 漏斗域图自注册

import 此模块即把选品漏斗域图注册到 domain_graph_registry，
由 backend/domains/__init__.py 统一触发；Main Graph builder 自动布线。

路由接入现状（重要）：
  - router_node.py 当前由 capability_registry 重构会话持有未提交改动，
    本域预过滤（selection_funnel_prefilter）**暂未插入**其链路 ——
    接线为单行插入，待重构落地后按
    docs/coordination/2026-09-17-selection-funnel-domain.md 的清单执行。
  - 域开关 SELECTION_FUNNEL_ENABLED 默认 false，未接线期间无流量风险。
"""
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.graph.selection_funnel_graph_node import (
    selection_funnel_graph_node,
)

domain_graph_registry.register(DomainGraph(
    name="selection_funnel",
    node_name="selection_funnel_graph_node",
    label="智能选品漏斗执行",
    adapter=selection_funnel_graph_node,
))
