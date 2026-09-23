"""tests/test_task_checkpoint_recovery.py — Phase1 Task Runtime Step2 恢复语义。

停点 2 验收：
- 正常执行 A → B → C → SUCCESS（每节点执行计数 = 1）
- A → B 后 crash（Worker kill 模拟：B checkpoint 落库后流中断）→ 重启后
  resume → 只有 C 执行（A/B 计数不变）
- B 内部 crash（Celery 重试语义）→ 重试只重跑未完成的 B，A 不重跑
- task.current_node / checkpoint_id 与实际一致（checkpoint_id 为 LangGraph
  真实 id，不再是 thread_id 占位）
- "API/Worker 重启" 用「新建 PostgresSaver 连接 + 重新编译图」模拟——
  checkpoint 持久在 agent_memory（PostgresSaver），跨进程可恢复

线程隔离：每用例独立 task_id → thread_id（task-{uuid}），LangGraph
checkpoint 行与 agent_checkpoints 历史在 teardown 按线程精确清理。
"""
from __future__ import annotations

import uuid
from typing import TypedDict

import pytest

from backend.models.task import TaskRecord, TaskStatus


# ── STOP D P0（2026-09-23）：TaskGraphExecutor 执行时解析授权 ──
# 本文件测 fencing/恢复/编排语义，不是授权本身；auth.users 数据源
# mock 为合法 editor（tenant 与 pg fixture 的 default 租户一致），
# resolve_task_authorization 的判定逻辑仍真实执行。
@pytest.fixture(autouse=True)
def _task_auth_enabled(monkeypatch):
    import backend.security.task_authorization as _ta
    from backend.security.authorization import build_tool_authorization_context

    # 本文件用例的 user 是随机串（非 auth.users 数字 id 口径），授权解析
    # 整体替换为合法 editor 上下文；task_executor 的注入/刷新逻辑仍真实执行
    ctx = build_tool_authorization_context(
        user_id="900001", department="ecom", tenant_id="default",
        roles=("editor",))
    monkeypatch.setattr(_ta, "resolve_task_authorization",
                        lambda uid, tid: ctx)


def _postgres_saver_available() -> bool:
    try:
        import psycopg  # noqa: F401
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401

        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def pg():
    """真实 agent_memory 连接；不可达或缺 PostgresSaver 驱动则跳过整组。"""
    pytest.importorskip("psycopg")
    if not _postgres_saver_available():
        pytest.skip("langgraph-checkpoint-postgres 不可用，跳过恢复测试")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
        task_service._conn().close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过恢复测试: {e}")
    from backend.services import task_service

    return task_service


class ABCState(TypedDict, total=False):
    question: str
    step_results: dict
    final_answer: str


def _make_abc_graph():
    """A→B→C 三节点图（每次调用 = 一次全新"进程"：新 PostgresSaver + 新编译图）。

    fail 字典控制节点内 crash（Celery 重试语义用）；calls 为节点执行计数。
    """
    from backend.orchestration.checkpoint.task_executor import (
        build_task_checkpointer)
    from langgraph.graph import END, START, StateGraph

    calls = {"step_a": 0, "step_b": 0, "step_c": 0}
    fail = {"step_a": False, "step_b": False, "step_c": False}

    def _mk(name: str):
        def _node(state: dict) -> dict:
            calls[name] += 1
            if fail[name]:
                raise RuntimeError(f"boom: {name} crashed")
            out = {"step_results": {**state.get("step_results", {}),
                                    name: "ok"}}
            if name == "step_c":
                out["final_answer"] = "done"   # 免触发 fallback 汇总（要求 dict 型结果）
            return out
        return _node

    wf = StateGraph(ABCState)
    wf.add_node("step_a", _mk("step_a"))
    wf.add_node("step_b", _mk("step_b"))
    wf.add_node("step_c", _mk("step_c"))
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", "step_b")
    wf.add_edge("step_b", "step_c")
    wf.add_edge("step_c", END)
    graph = wf.compile(checkpointer=build_task_checkpointer())
    return graph, calls, fail


def _cleanup(pg, task_id: str, thread_id: str) -> None:
    """按线程/task 精确清理 LangGraph checkpoint 表与节点历史。"""
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
        cur.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s",
                    (thread_id,))
        cur.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s",
                    (thread_id,))
        cur.execute("DELETE FROM agent_checkpoints WHERE task_id = %s",
                    (task_id,))


@pytest.fixture()
def env(pg):
    """每用例：独立任务行 + 图工厂 + teardown 清理。"""
    user = f"rec-user-{uuid.uuid4().hex[:8]}"
    record = pg.create_task(user, "A→B→C 恢复测试")
    state = {"record": record, "graph": None, "calls": None, "fail": None}

    def _new_graph():
        graph, calls, fail = _make_abc_graph()
        state["graph"] = graph
        state["calls"] = calls
        state["fail"] = fail
        return graph, calls, fail

    state["new_graph"] = _new_graph
    _new_graph()
    yield state
    _cleanup(pg, record.id, record.thread_id)


def _config(record: TaskRecord) -> dict:
    return {"recursion_limit": 80,
            "configurable": {"thread_id": record.thread_id}}


def _executor(graph):
    from backend.orchestration.checkpoint import TaskGraphExecutor

    return TaskGraphExecutor(graph=graph, poll_control_flags=False)


# ═══════════════════════════════════════════════════
# 正常执行：A → B → C → SUCCESS
# ═══════════════════════════════════════════════════

def test_normal_run_abc(pg, env):
    record, graph, calls = env["record"], env["graph"], env["calls"]
    _executor(graph).execute(record)
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}

    row = pg.get_task(record.id)
    assert row.status == TaskStatus.SUCCESS
    assert row.current_node == "step_c"          # 与实际最后完成节点一致
    # checkpoint_id = LangGraph 真实 checkpoint id（非 thread_id 占位）
    snap = graph.get_state(_config(record))
    real_cp = snap.config["configurable"]["checkpoint_id"]
    assert row.checkpoint_id == real_cp
    assert row.checkpoint_id != row.thread_id


# ═══════════════════════════════════════════════════
# B 完成 checkpoint 落库后 kill → 重启 → 只有 C 执行
# ═══════════════════════════════════════════════════

def test_kill_after_b_resume_runs_only_c(pg, env):
    record = env["record"]
    graph, calls = env["graph"], env["calls"]
    task_service = pg

    # ── 第一次"进程"：手动驱动流，B 的节点边界写库后立即中断（= kill）──
    from backend.orchestration.graph.events import make_initial_state

    task_service.update_status(record.id, TaskStatus.RUNNING, progress="开始执行")
    payload = make_initial_state(
        "kill 恢复测试", record.id, "default", messages=[],
        guard_result=None, user_id=record.user_id)
    seen_b = False
    for event in graph.stream(payload, config=_config(record),
                              stream_mode="updates"):
        for node_name, node_output in event.items():
            if node_name.startswith("__"):
                continue
            # 复刻 executor 节点边界的落库（kill 前已完成的持久化痕迹）
            task_service.update_progress(record.id, node_name,
                                         progress=f"节点 {node_name} 完成")
            task_service.append_checkpoint(record.id, node_name,
                                           node_output or {})
            if node_name == "step_b":
                seen_b = True
        if seen_b:
            break                                 # Worker 在 C 开始前被 kill
    assert seen_b
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 0}

    # kill 后无 _fail 落库：状态停在 RUNNING（硬杀现场）
    assert task_service.get_task(record.id).status == TaskStatus.RUNNING

    # ── 重启后的"新进程"：全新 PostgresSaver 连接 + 重新编译图 ──
    graph2, calls2, _ = env["new_graph"]()
    # checkpoint 跨进程仍在（新 saver 实例可读）
    assert graph2.get_state(_config(record)).values.get("step_results", {}) \
        .get("step_a") == "ok"

    output = _executor(graph2).execute(task_service.get_task(record.id))
    # 新"进程"计数：只有 C 执行（A/B 计数为 0 = 未重跑）；旧进程从未跑到 C
    assert calls2 == {"step_a": 0, "step_b": 0, "step_c": 1}
    assert calls["step_c"] == 0
    assert output["answer"] == "done"
    row = task_service.get_task(record.id)
    assert row.status == TaskStatus.SUCCESS
    assert row.current_node == "step_c"
    assert row.output["step_results"].get("step_a") == "ok"    # 恢复自 checkpoint 状态


# ═══════════════════════════════════════════════════
# B 内部 crash（Celery 重试语义）：只重跑未完成的 B
# ═══════════════════════════════════════════════════

def test_crash_in_b_retry_reexecutes_b_only(pg, env):
    record, graph, calls, fail = (
        env["record"], env["graph"], env["calls"], env["fail"])
    fail["step_b"] = True
    with pytest.raises(RuntimeError, match="step_b"):
        _executor(graph).execute(record)
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 0}

    # impl 层重试语义：FAILED → 显式回 PENDING → 续跑
    pg.update_status(record.id, TaskStatus.FAILED, error_message="boom")
    pg.update_status(record.id, TaskStatus.PENDING, progress="重试回队")
    fail["step_b"] = False
    _executor(graph).execute(pg.get_task(record.id))

    assert calls == {"step_a": 1, "step_b": 2, "step_c": 1}   # A 不重跑；B 补跑成功
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.SUCCESS
    assert row.current_node == "step_c"


# ═══════════════════════════════════════════════════
# checkpoint 跨"重启"持久（API/Worker 重启不丢）
# ═══════════════════════════════════════════════════

def test_checkpoints_survive_process_restart(pg, env):
    record, graph, calls = env["record"], env["graph"], env["calls"]
    _executor(graph).execute(record)

    graph2, calls2, _ = env["new_graph"]()     # 模拟进程重启
    snap = graph2.get_state(_config(record))
    results = snap.values.get("step_results", {})
    assert results == {"step_a": "ok", "step_b": "ok", "step_c": "ok"}
    # 新进程拾取已 SUCCESS 的任务：终态短路零执行（calls2 全 0 = 重启后零重复执行）
    _executor(graph2).execute(pg.get_task(record.id))
    assert calls2 == {"step_a": 0, "step_b": 0, "step_c": 0}
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}   # 唯一执行发生在原进程
    assert pg.get_task(record.id).status == TaskStatus.SUCCESS
