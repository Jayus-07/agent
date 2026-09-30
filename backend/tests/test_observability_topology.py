"""可观测性拓扑守护 — 防节点标签/清单再次漂移（结构病 P1-1）。

背景：``observability/topology.py`` 曾手写一份节点标签与节点清单，与 builder
真源漂移——残留 ``sql_worker``/``rag_worker``/``report_worker`` 旧名（真实为
``*_skill``），缺 router/tool_selector/skill_executor/workflow_executor/
general_chat 与 5 张域图，注释却自称「与 graph.py _NODE_LABELS 保持一致」；
而 ``GET /observability/graph`` 正是把这份过期数据下发出去。
详见 docs/reports/2026-09-30-结构病代码审查报告.md。

守护口径：
  1. 标签表 = **派生视图**（结构守护），不是手写字典；
  2. 取值为**字面量**锁死（不用重算式，否则是恒真断言）；
  3. edges 的端点必须**真实存在**（断链守护——修复前 sql_worker 在此处变红）。
"""
from __future__ import annotations

import re
from pathlib import Path

import backend.domains  # noqa: F401  # import 即触发域图自注册（与 /agents 同前置）
from backend.observability.topology import (
    _GRAPH_EDGE_SPEC,
    NODE_LABELS,
    _DerivedNodeLabels,
    graph_topology,
)

# 真实节点集 = builder 真源（含运行时聚合的域图节点）+ LangGraph 哨兵节点
from backend.orchestration.graph.builder import _NODE_LABELS

_SENTINELS = {"__start__", "__end__"}


def test_node_labels_is_derived_view_not_handwritten_dict():
    """结构守护：改回手写字典即等于给「漏改」重新留口子。"""
    assert isinstance(NODE_LABELS, _DerivedNodeLabels), (
        "NODE_LABELS 必须是派生视图；手写字典会随时间与 builder 漂移"
    )


def test_node_labels_values_locked_to_literals():
    """契约值锁死（字面量，非重算式）。"""
    assert NODE_LABELS["sql_skill"] == "数据库查询"
    assert NODE_LABELS["rag_skill"] == "知识库检索"
    assert NODE_LABELS["report_skill"] == "报告生成"
    assert NODE_LABELS["planner"] == "任务规划"
    assert NODE_LABELS["supervisor"] == "调度决策"


def test_node_labels_exactly_mirror_builder():
    """派生结果必须逐字等于 builder 真源（派生的源接错东西时这里会红）。"""
    assert dict(NODE_LABELS) == dict(_NODE_LABELS)


def test_edges_endpoints_exist_in_real_node_set():
    """断链守护：edges 端点必须真实存在。

    修复前 topology 用的是 ``sql_worker`` 等旧名——本断言会直接报出断链端点。
    """
    real = set(_NODE_LABELS) | _SENTINELS
    for src, dst, _label in _GRAPH_EDGE_SPEC:
        assert src in real, f"edges 起点不是真实节点（断链）: {src}"
        assert dst in real, f"edges 终点不是真实节点（断链）: {dst}"


def test_topology_nodes_cover_all_edge_endpoints():
    """图自洽：nodes 清单必须涵盖 edges 引用的全部端点。"""
    ids = {n["id"] for n in graph_topology()["nodes"]}
    for src, dst, _label in _GRAPH_EDGE_SPEC:
        assert src in ids, f"nodes 缺 edges 起点: {src}"
        assert dst in ids, f"nodes 缺 edges 终点: {dst}"


def test_derived_nodes_carry_label_and_type():
    """nodes 派生结果形状：每项含 id / label / type，且节点名取自真源。"""
    nodes = graph_topology()["nodes"]
    assert nodes, "节点清单不应为空"
    by_id = {n["id"]: n for n in nodes}
    assert by_id["sql_skill"]["label"] == "数据库查询"
    assert by_id["sql_skill"]["type"] == "skill"
    assert by_id["planner"]["type"] == "llm"
    assert by_id["router"]["type"] == "router"


def test_no_stale_worker_names_in_topology_source():
    """防写回：**代码**里不得再出现过期节点名（注释/docstring 里的历史说明不算）。

    修复前 topology.py 的 GRAPH_TOPOLOGY / NODE_LABELS 确实写着这些旧名；
    本断言剥离注释与三引号块后再扫，避免把「历史说明」误判成回退。
    """
    raw = (
        Path(__file__).resolve().parents[1] / "observability" / "topology.py"
    ).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", raw, flags=re.S)
    code = re.sub(r"'''.*?'''", "", code, flags=re.S)
    code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
    for stale in ("sql_worker", "rag_worker", "report_worker"):
        assert stale not in code, f"topology.py 代码回退到过期节点名: {stale}"
