"""可观测性 — 图拓扑定义

历史：原文件含 TraceStore（内存环形缓冲区 + TraceEvent/Trace dataclass）。
该类写入方法（start_trace / add_event / end_trace）从未被调用（死代码），
实际数据源统一在 `backend.rag.tracer.TraceCollector`。
删除原因：
1. TraceStore 写方法是死代码（grep 0 处调用）
2. TraceStore 读方法可由 TraceCollector 完全替代
3. 减少未来持久化迁移工作量（1 套 store vs 2 套）

保留：GRAPH_TOPOLOGY + NODE_LABELS（LangGraph 多 Agent 拓扑，独立有用）
新增：INDEXING_TOPOLOGY + INDEXING_LABELS（Knowledge Index 流水线拓扑，Phase 1）
"""


# =====================================================
# 图拓扑静态定义（供前端渲染 / observability/graph API）
# =====================================================

GRAPH_TOPOLOGY = {
    "nodes": [
        {"id": "planner",       "label": "任务规划",   "type": "llm"},
        {"id": "critique",      "label": "计划审查",   "type": "llm"},
        {"id": "supervisor",    "label": "调度决策",   "type": "router"},
        {"id": "sql_worker",    "label": "数据库查询", "type": "worker"},
        {"id": "rag_worker",    "label": "知识库检索", "type": "worker"},
        {"id": "report_worker", "label": "报告生成",   "type": "worker"},
        {"id": "reporter",      "label": "结果汇总",   "type": "llm"},
    ],
    "edges": [
        {"id": "e1",  "source": "planner",       "target": "critique",      "label": ""},
        {"id": "e2",  "source": "critique",       "target": "supervisor",   "label": "有任务"},
        {"id": "e3",  "source": "critique",       "target": "reporter",     "label": "空计划"},
        {"id": "e4",  "source": "supervisor",     "target": "sql_worker",   "label": "dispatch"},
        {"id": "e5",  "source": "supervisor",     "target": "rag_worker",   "label": "dispatch"},
        {"id": "e6",  "source": "supervisor",     "target": "report_worker","label": "dispatch"},
        {"id": "e7",  "source": "sql_worker",     "target": "supervisor",   "label": "完成"},
        {"id": "e8",  "source": "rag_worker",     "target": "supervisor",   "label": "完成"},
        {"id": "e9",  "source": "report_worker",  "target": "supervisor",   "label": "完成"},
        {"id": "e10", "source": "supervisor",     "target": "reporter",     "label": "全部完成"},
    ],
}

# 节点标签映射（与 graph.py _NODE_LABELS 保持一致）
NODE_LABELS = {
    "planner":       "任务规划",
    "critique":      "计划审查",
    "supervisor":    "调度决策",
    "sql_worker":    "数据库查询",
    "rag_worker":    "知识库检索",
    "report_worker": "报告生成",
    "reporter":      "结果汇总",
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
# 全局拓扑注册表（前端通过 workflow_kind 路由到对应拓扑）
# =====================================================

TOPOLOGY_REGISTRY = {
    "langgraph_workflow": GRAPH_TOPOLOGY,
    "knowledge_index":   INDEXING_TOPOLOGY,
}


def get_topology(workflow_kind: str) -> dict:
    """根据 workflow_kind 返回对应拓扑。前端渲染时按此路由。"""
    return TOPOLOGY_REGISTRY.get(workflow_kind, GRAPH_TOPOLOGY)