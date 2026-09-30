"""可观测性 — 图拓扑定义

历史：原文件含 TraceStore（内存环形缓冲区 + TraceEvent/Trace dataclass）。
该类写入方法（start_trace / add_event / end_trace）从未被调用（死代码），
实际数据源统一在 `backend.rag.tracer.TraceCollector`。
删除原因：
1. TraceStore 写方法是死代码（grep 0 处调用）
2. TraceStore 读方法可由 TraceCollector 完全替代
3. 减少未来持久化迁移工作量（1 套 store vs 2 套）

保留：graph_topology() + NODE_LABELS（LangGraph 多 Agent 拓扑，独立有用）
新增：INDEXING_TOPOLOGY + INDEXING_LABELS（Knowledge Index 流水线拓扑，Phase 1）

── 2026-09-30 结构病修复（docs/reports/2026-09-30-结构病代码审查报告.md P1-1）──
本文件过去手写了一份节点标签与节点清单，与 builder 真源漂移：
残留 `sql_worker`/`rag_worker`/`report_worker` 旧名（真实为 `*_skill`），
缺 router/tool_selector/skill_executor/workflow_executor/general_chat 与 5 张域图，
注释却自称「与 graph.py _NODE_LABELS 保持一致」——而 `GET /observability/graph`
正是把这份过期数据下发给外面（诊断：旧名只在定义处与消费处出现，运行时不产出）。

现状：标签与节点清单**全部改为从 builder 真源派生（零手写）**；edges 保留为
人工语义示意（带「有任务/空计划」这类分支语义标签，机器导不出来），但其端点
必须存在于真源节点集，由 tests/test_observability_topology.py 断言守住。
"""
from __future__ import annotations

from collections.abc import Mapping


class _DerivedNodeLabels(Mapping):
    """节点名 → 中文标签。活视图：每次读取都从 builder 真源现算，杜绝第二份副本。

    延迟导入是刻意的：本模块在 `backend.observability` 包初始化期被导入，
    而 builder 反向依赖 `observability.trace_middleware`，模块级 import 会成环。

    `builder._NODE_LABELS` 是**运行时聚合**的（模块级字面量 + build_graph 时注册的
    域图节点），所以必须现算——取模块级快照会在应用尚未 build 时拿到半张表。
    """

    __slots__ = ()

    @staticmethod
    def _source() -> Mapping[str, str]:
        from backend.orchestration.graph.builder import _NODE_LABELS

        return _NODE_LABELS

    def __getitem__(self, key: str) -> str:
        try:
            return self._source()[key]
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self._source())

    def __len__(self) -> int:
        return len(self._source())

    def __repr__(self) -> str:
        return f"NodeLabels(derived={dict(self._source())!r})"


# 由 builder._NODE_LABELS 派生（零手写）。
NODE_LABELS: Mapping[str, str] = _DerivedNodeLabels()


# =====================================================
# 图节点清单（派生） + 连线示意（人工）
# =====================================================

def _node_kind(name: str) -> str:
    """节点名 → 展示分类（按命名派生，不另立手写表）。"""
    if name == "router":
        return "router"
    if name.endswith("_graph_node"):
        return "domain_graph"
    if name.endswith("_skill"):
        return "skill"
    return "llm"


def _derived_nodes() -> list[dict]:
    """节点清单 —— 与 `GET /agents` 同源（内置 + Skill + 域图，三者并集）。

    域图节点**显式并入注册表**，不依赖「应用是否已经 build 过图」：
    `builder._NODE_LABELS` 里的域图标签是 build_graph() 期间写入的，
    若在此前调用（如启动探针）会漏掉全部域图节点。
    """
    from backend.orchestration.capability_registry import tool_registry
    from backend.orchestration.domain_registry import domain_graph_registry

    labels: dict[str, str] = dict(NODE_LABELS)
    # 无中文标签的 Skill：回退节点名（与 /agents 端点的 `.get(name, name)` 同口径）
    for name in tool_registry.get_skill_nodes():
        labels.setdefault(name, name)

    nodes = [
        {"id": name, "label": label, "type": _node_kind(name)}
        for name, label in labels.items()
    ]
    seen = {n["id"] for n in nodes}
    for domain in domain_graph_registry.get_all().values():
        if domain.node_name not in seen:
            nodes.append(
                {"id": domain.node_name, "label": domain.label, "type": "domain_graph"}
            )
    return nodes


# 人工语义示意：LangGraph 编译图导不出「有任务 / 空计划 / dispatch」这类分支语义，
# 且 router / supervisor 的动态派发在静态图上只是条件边。
# **端点必须真实存在** —— 由 tests/test_observability_topology.py 守护（防再次断链）。
_GRAPH_EDGE_SPEC: list[tuple[str, str, str]] = [
    ("router", "planner", "复杂任务"),
    ("router", "tool_selector", "直接执行"),
    ("router", "general_chat", "寒暄直答"),
    ("planner", "critique", ""),
    ("critique", "supervisor", "有任务"),
    ("critique", "reporter", "空计划"),
    ("supervisor", "sql_skill", "dispatch"),
    ("supervisor", "rag_skill", "dispatch"),
    ("supervisor", "report_skill", "dispatch"),
    ("sql_skill", "supervisor", "完成"),
    ("rag_skill", "supervisor", "完成"),
    ("report_skill", "supervisor", "完成"),
    ("supervisor", "reporter", "全部完成"),
]


def graph_topology() -> dict:
    """LangGraph 主图拓扑：nodes 派生自真源，edges 为人工语义示意。"""
    return {
        "nodes": _derived_nodes(),
        "edges": [
            {"id": f"e{i}", "source": src, "target": dst, "label": label}
            for i, (src, dst, label) in enumerate(_GRAPH_EDGE_SPEC, start=1)
        ],
    }


# =====================================================
# Knowledge Index 流水线拓扑（Phase 1 — 与 indexer.py 的 6 个 span 对应）
# =====================================================

INDEXING_TOPOLOGY = {
    "nodes": [
        {"id": "index_upload",      "label": "上传",     "type": "io"},
        {"id": "index_parse",       "label": "解析",     "type": "parse"},
        {"id": "index_chunk",       "label": "分块",     "type": "chunk"},
        {"id": "index_embed",       "label": "向量化",   "type": "embedding"},
        {"id": "index_vector_db",   "label": "向量库",   "type": "vector_db"},
        {"id": "index_metadata",    "label": "元数据",   "type": "llm"},
    ],
    "edges": [
        {"id": "ie1", "source": "index_upload",    "target": "index_parse",     "label": ""},
        {"id": "ie2", "source": "index_parse",     "target": "index_chunk",     "label": ""},
        {"id": "ie3", "source": "index_chunk",     "target": "index_embed",     "label": ""},
        {"id": "ie4", "source": "index_embed",     "target": "index_vector_db", "label": ""},
        {"id": "ie5", "source": "index_vector_db", "target": "index_metadata",  "label": ""},
    ],
}

INDEXING_LABELS = {
    "index_upload":     "上传",
    "index_parse":      "解析",
    "index_chunk":      "分块",
    "index_embed":      "向量化",
    "index_vector_db":  "向量库",
    "index_metadata":   "元数据",
}


# =====================================================
# 拓扑分发（前端 / API 按 workflow_kind 取对应拓扑）
# =====================================================

_INDEXING_KINDS = frozenset({"knowledge_index", "indexing"})


def get_topology(workflow_kind: str) -> dict:
    """根据 workflow_kind 返回对应拓扑。

    - knowledge_index → 索引流水线静态拓扑
    - 其余（含 langgraph_workflow）→ 主图拓扑（节点派生自真源）
    """
    if workflow_kind in _INDEXING_KINDS:
        return INDEXING_TOPOLOGY
    return graph_topology()


__all__ = [
    "NODE_LABELS",
    "graph_topology",
    "get_topology",
    "INDEXING_TOPOLOGY",
    "INDEXING_LABELS",
]
