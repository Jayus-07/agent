"""旅游链路必须自证 runtime_domain，否则 trace 来源三分类会误判。

回归背景：`classify_trace_source` 只认 `tags.runtime_domain` / `tags.domain`。
主图 Router 由 prefilter_chain → record_router_decision 写入该标签，但旅游
链路走独立入口，不经过主图 Router，只写 travel_* 业务标签。结果线上 28/28
条旅游 trace 全被判成「AI 助手」（workflow_name=="agent" 兜底分支）。

本文件覆盖写入侧：旅游节点必须在取消/完成两条路径都声明 runtime_domain。
分类器侧的行为另见 tests/observability/test_trace_source.py。
"""
from __future__ import annotations

from backend.observability.trace_source import SOURCE_TRAVEL, classify_trace_source
from backend.observability.tracer import trace_collector


def test_stamp_execution_tags_sets_runtime_domain():
    """完成路径 _stamp_execution_tags 写入 runtime_domain=travel。"""
    from backend.orchestration.graph.travel_graph_node import _stamp_execution_tags

    trace_collector.clear_for_test()
    trace = trace_collector.start("规划杭州三日游", session_id="travel-domain")
    try:
        _stamp_execution_tags(
            {"conversation_id": "c-1"},
            {"status": "success", "final_answer": "行程已生成"},
            run_id="travel-x",
        )
    finally:
        trace_collector.clear_for_test()

    assert trace.tags["runtime_domain"] == "travel"
    # 端到端：分类器据此判为旅游域，而不是 AI 助手
    assert classify_trace_source("agent", trace.tags) == SOURCE_TRAVEL


def test_cancel_path_declares_runtime_domain():
    """取消路径的埋点也必须带 runtime_domain。

    取消分支需要 conversation context 里存在活跃 run 才能走到埋点，依赖
    仓库状态、成本高且脆弱；这里用源码契约断言兜底——只要该分支存在
    取消埋点，就必须同时声明 runtime_domain，防止后续被单独删掉。
    """
    import inspect

    from backend.orchestration.graph import travel_graph_node as mod

    src = inspect.getsource(mod._maybe_cancel_active_run)
    assert 'trace.tags["travel_status"] = "cancelled"' in src, (
        "取消埋点被移除或改写，本契约需同步更新"
    )
    assert 'trace.tags["runtime_domain"] = "travel"' in src, (
        "取消路径缺 runtime_domain：该轮 trace 会被判成 AI 助手"
    )


def test_no_travel_branch_bypasses_runtime_domain():
    """旅游节点里凡是写 travel_status 的埋点，都要同时写 runtime_domain。"""
    import inspect

    from backend.orchestration.graph import travel_graph_node as mod

    src = inspect.getsource(mod)
    status_lines = [
        line for line in src.splitlines()
        if "trace.tags[" in line and "travel_status" in line
    ]
    assert status_lines, "未找到任何 travel_status 埋点，契约已失效"
    domain_lines = [
        line for line in src.splitlines()
        if "trace.tags[" in line and "runtime_domain" in line
    ]
    assert len(domain_lines) >= len(status_lines), (
        f"travel_status 埋点 {len(status_lines)} 处，但 runtime_domain 只有 "
        f"{len(domain_lines)} 处——存在会被误判为 AI 助手的路径"
    )
