"""余额暂停不能吞成成功，也不能重跑检查点前的节点。"""
from types import SimpleNamespace
from typing import TypedDict

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.infra.llm import budget
from backend.infra.llm.quota import QuotaExceeded
from backend.models.task import TaskRecord, TaskStatus
from backend.observability.trace_middleware import TraceMiddleware


@pytest.fixture
def request_budget():
    state = budget.RequestBudget(mode="enforce")
    budget._states['long-task-quota-test'] = state
    token = budget._current_request_id.set('long-task-quota-test')
    yield state
    budget._current_request_id.reset(token)
    budget._states.pop('long-task-quota-test', None)


def test_swallowed_quota_does_not_commit_node_checkpoint(request_budget):
    class State(TypedDict, total=False):
        final_answer: str

    calls = {'a': 0, 'b': 0}
    exhausted = [True]

    def a(state):
        calls['a'] += 1
        return {'final_answer': '前一步已完成'}

    def b(state):
        calls['b'] += 1
        if exhausted[0]:
            # 模拟工具把额度拒绝转成降级返回；节点边界仍必须阻断提交。
            budget.note_quota_blocked(QuotaExceeded('user:42', 'day'))
            return {'final_answer': '降级结果，不能当成功'}
        return {'final_answer': '继续后完成'}

    builder = StateGraph(State)
    builder.add_node('a', a)
    builder.add_node('b', TraceMiddleware().wrap_sync_node('b', b))
    builder.add_edge(START, 'a')
    builder.add_edge('a', 'b')
    builder.add_edge('b', END)
    graph = builder.compile(checkpointer=MemorySaver())
    config = {'configurable': {'thread_id': 'quota-checkpoint'}}
    with pytest.raises(QuotaExceeded):
        graph.invoke({}, config)
    snapshot = graph.get_state(config)
    assert snapshot.next == ('b',)
    assert snapshot.values['final_answer'] == '前一步已完成'
    # 恢复执行会建立新的预算上下文；这里模拟补足额度后的新 execution。
    request_budget.quota_error = None
    exhausted[0] = False
    assert graph.invoke(None, config)['final_answer'] == '继续后完成'
    assert calls == {'a': 1, 'b': 2}


@pytest.mark.parametrize('scope,biz_type,paused', [
    ('user', 'agent_chat_long', True),
    ('tenant', 'agent_chat_long', False),
    ('user', '', False),
])
def test_worker_only_pauses_long_task_user_quota(monkeypatch, scope, biz_type, paused):
    from backend.orchestration import checkpoint
    from backend.services import task_service
    from backend.tasks import admission, agent_tasks, lease_heartbeat

    record = TaskRecord(id='quota-task', user_id='42', tenant_id='t1', biz_type=biz_type)
    error = QuotaExceeded(f'{scope}:42', 'day')

    def execute(*a, **kw):
        raise error

    writes = []
    monkeypatch.setattr(task_service, 'get_task', lambda *a: record)
    monkeypatch.setattr(task_service, 'try_acquire_lease', lambda *a, **kw: 'lease')
    monkeypatch.setattr(agent_tasks, '_record_queue_wait', lambda *a: None)
    monkeypatch.setattr(agent_tasks, '_fail', lambda *a, **kw: writes.append((a, kw)))
    monkeypatch.setattr(agent_tasks, '_finalize_failure', lambda *a, **kw: False)
    monkeypatch.setattr(admission, 'acquire_for_execution', lambda *a, **kw: SimpleNamespace(allowed=True))
    monkeypatch.setattr(admission, 'release_for_execution', lambda *a, **kw: None)
    monkeypatch.setattr(checkpoint, 'TaskGraphExecutor', lambda: SimpleNamespace(execute=execute))
    monkeypatch.setattr(lease_heartbeat, 'LeaseHeartbeat', lambda *a: SimpleNamespace(start=lambda: None, stop=lambda: None))
    if paused:
        assert agent_tasks.execute_agent_task_impl(record.id)['status'] == 'PAUSED'
        assert writes[0][0][2] == TaskStatus.PAUSED
        assert writes[0][1]['error_type'] == 'user_balance_insufficient'
    else:
        with pytest.raises(QuotaExceeded):
            agent_tasks.execute_agent_task_impl(record.id)
        assert writes == []


def test_remote_rag_quota_retains_control_semantics(request_budget):
    from backend.rag.client import RAGServiceProxy

    transport = httpx.MockTransport(lambda req: httpx.Response(
        402, headers={'X-Agent-Quota-Scope': 'user'},
        json={'code': 'USER_BALANCE_INSUFFICIENT'},
    ))
    client = RAGServiceProxy(base_url='http://test', transport=transport)
    with pytest.raises(QuotaExceeded):
        client.ask('测试', user_id='42', tenant_id='t1')
    assert request_budget.quota_error.subject_id == 'user:42'
