"""在已校验的业务计划边界移交任务，不以问题字数判断执行方式。"""
from contextlib import closing


def seed_task(task_id: str, state: dict, node: str) -> None:
    """派发前持久化完整计划与节点结果，Worker 从原节点之后恢复。"""
    from backend.orchestration.checkpoint.task_executor import build_task_graph
    from backend.services import task_service

    if node != "critique" or not is_persistent_plan(state):
        raise ValueError("只能移交已校验的多步骤业务计划")
    snapshot = dict(state)
    context = snapshot.get("request_context")
    if hasattr(context, "checkpoint_safe"):
        snapshot["request_context"] = context.checkpoint_safe()
    graph = build_task_graph()
    config = {"configurable": {"thread_id": f"task-{task_id}"}}
    saved = graph.update_state(config, snapshot, as_node=node)
    task_service.append_checkpoint(task_id, node, {"handoff": True})
    task_service.update_progress(
        task_id, node, "业务计划已持久化，等待后台执行",
        checkpoint_id=saved["configurable"]["checkpoint_id"],
    )


def is_persistent_plan(state: dict) -> bool:
    """仅多个真实业务能力节点构成持久任务，排除汇总和普通生成。"""
    nodes = (state.get("plan") or {}).get("nodes") or {}
    if not isinstance(nodes, dict):
        return False
    business = [
        node for node in nodes.values()
        if isinstance(node, dict) and node.get("capability")
        and node["capability"] not in {"general_chat"}
    ]
    return len(business) >= 2


def stream_with_handoff(graph, state, config, handoff=None):
    """消费完整状态快照；移交成功后不再推进前台图的下一个业务节点。"""
    if handoff is None:
        yield from graph.stream(state, config=config)
        return
    completed = None
    pending_state = None
    # values 是 reducer 合并后的权威状态，不能手工拼接 updates 冒充检查点。
    with closing(graph.stream(state, config=config,
                              stream_mode=["updates", "values"])) as events:
        for mode, value in events:
            if mode == "updates":
                completed = next(iter(value), None)
                yield value
            elif mode == "values" and completed == "critique":
                if is_persistent_plan(value):
                    pending_state = value
                    break
    if pending_state is not None:
        # 先关闭前台执行器，再保存并派发；失败向上抛出，禁止偷偷前台重跑。
        task = handoff(pending_state, "critique")
        yield {"__task_handoff": task}
