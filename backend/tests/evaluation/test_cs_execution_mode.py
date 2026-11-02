import pytest
from pydantic import ValidationError

from backend.evaluation.config import EvalConfig


@pytest.mark.parametrize('values', [
    {'module': 'cs', 'live': False},
    {'module': 'rag', 'live': True},
])
def test_full_path_requires_explicit_live_cs(values):
    with pytest.raises(ValidationError, match='full_path'):
        EvalConfig(cs_mode='full_path', **values)


@pytest.mark.parametrize('limit', ['0', '10.01', 'NaN', 'Infinity'])
def test_cs_run_cap_cannot_exceed_user_approved_limit(limit):
    with pytest.raises(ValidationError):
        EvalConfig(module='cs', live=True, cs_max_cost_cny=limit)


@pytest.mark.parametrize('mode', ['off', 'observe'])
def test_live_cs_stops_before_cases_without_enforced_budget(monkeypatch, tmp_path, mode):
    from uuid import uuid4

    from backend.evaluation import storage
    from backend.evaluation.service import BudgetBlockedError, EvaluationService
    from backend.infra.llm import budget

    monkeypatch.setattr(storage, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(budget, '_config_mode', lambda: mode)
    service = EvaluationService()
    service._runners_registered = True

    def forbidden(*args):
        pytest.fail('预算保护不可用时不能执行样本')

    monkeypatch.setattr(service, '_evaluate_cases', forbidden)
    with pytest.raises(BudgetBlockedError, match='强制预算'):
        service.evaluate(EvalConfig(module='cs', live=True, run_id=uuid4().hex))
    assert budget.current_run_budget() is None


def test_cli_and_public_runner_preserve_full_path(monkeypatch):
    from backend.evaluation.cli import build_parser
    from backend.evaluation.runner import run_all
    from backend.evaluation.service import EvaluationService

    args = build_parser().parse_args(['cs', '--live', '--cs-mode', 'full_path'])
    captured = []

    def evaluate(self, config):
        captured.append(config)
        return 'evaluated'

    monkeypatch.setattr(EvaluationService, 'evaluate', evaluate)
    assert run_all(module=args.module, live=args.live, cs_mode=args.cs_mode) == 'evaluated'
    assert captured[0].cs_mode == 'full_path'


def test_cs_dispatches_full_path_instead_of_graph_only(monkeypatch):
    from backend.evaluation.runners import cs

    captured = []
    monkeypatch.setattr(cs, '_run_live_full_path', lambda cases: captured.append(cases) or [])
    assert cs._run_cs([], live=True, mode='full_path') == []
    assert captured == [[]]


def test_full_path_replays_turns_and_does_not_reuse_previous_clarification(monkeypatch):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem

    calls = []

    def stream(self, question, session_id, **kwargs):
        calls.append((question, session_id))
        yield {'event': 'status', 'data': {'node': 'cs_graph_node'}}
        if question == '第一轮':
            yield {'event': 'clarification', 'data': {}}
        yield {'event': 'delta', 'data': {'content': '安全回答'}}

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)
    monkeypatch.setattr(MultiAgentSystem, 'stream_events', stream)
    case = Case(id='multi', question='第二轮', module='cs',
                expected={'cs_route': 'hit', 'next_action': 'clarify'},
                metadata={'turns': [
                    {'role': 'user', 'text': '第一轮'},
                    {'role': 'assistant', 'text': '不能注入的标注回复'},
                    {'role': 'user', 'text': '第二轮'},
                ]})
    result = cs._run_live_full_path([case])[0]
    assert [question for question, _ in calls] == ['第一轮', '第二轮']
    assert calls[0][1] == calls[1][1]
    assert result.actual['turn_count'] == 2
    assert result.status == 'fail'
    assert result.metrics['clarified'] == 0
    previous_session = calls[0][1]
    cs._run_live_full_path([case])
    assert calls[-1][1] != previous_session


@pytest.mark.parametrize('event', [
    {'event': 'error', 'data': {}},
    {'event': 'delta', 'data': {'content': '禁止承诺'}},
])
def test_full_path_rejects_runtime_error_and_forbidden_answer(monkeypatch, event):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)
    monkeypatch.setattr(MultiAgentSystem, 'stream_events', lambda *args, **kwargs: iter([event]))
    result = cs._run_live_full_path([
        Case(id='safety', question='请求', module='cs',
             expected={'forbidden_facts': ['禁止承诺']})
    ])[0]
    assert result.status != 'pass'
