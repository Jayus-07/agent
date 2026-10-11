"""主图 Checkpointer thread_id 不泄漏会话键且按执行保持唯一。"""

from backend.orchestration.graph.runner import _new_main_graph_thread_id


def test_main_graph_thread_ids_are_opaque_and_unique_per_execution():
    session_id = "private-session-id"
    user_id = "private-user-id"
    tenant_id = "private-tenant-id"

    first = _new_main_graph_thread_id()
    second = _new_main_graph_thread_id()

    assert first.startswith("agent-")
    assert second.startswith("agent-")
    assert first != second
    assert all(value not in first for value in (session_id, user_id, tenant_id))
    assert all(value not in second for value in (session_id, user_id, tenant_id))
    assert len(first.removeprefix("agent-")) == 32
