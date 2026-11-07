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
             expected={'must_not_contain': ['禁止承诺']})
    ])[0]
    assert result.status != 'pass'


@pytest.mark.parametrize(
    ('final_state', 'expected_action'),
    [
        ({'supervisor_decision': {'next_action': 'finish'}}, 'answer'),
        ({'supervisor_decision': {'next_action': 'finish'},
          'last_expert_result': {'data': {'decision': 'refuse'}}}, 'refuse'),
        ({'supervisor_decision': {'next_action': 'handoff'}}, 'handoff'),
        ({'supervisor_decision': {'next_action': 'pending'},
          'pending_action': {'status': 'pending_confirmation'}}, 'propose'),
        ({'supervisor_decision': {'next_action': 'pending'},
          'pending_action': {'status': 'need_info'}}, 'propose_with_gap'),
        ({
            'supervisor_decision': {'next_action': 'finish'},
            'pending_turn_slot_fill': True,
            'pending_action': {'status': 'need_info'},
            'last_expert_result': {
                'expert': 'action',
                'data': {'pending_action': {
                    'action_id': 'new-action',
                    'proposal_text': '退款申请',
                    'confirmation_state': 'pending',
                }},
            },
        }, 'propose'),
        ({
            'supervisor_decision': {'next_action': 'finish'},
            'pending_turn_slot_fill': True,
            'last_expert_result': {
                'expert': 'action',
                'data': {'pending_action': {'status': 'need_info'}},
            },
        }, 'propose_with_gap'),
        ({
            'supervisor_decision': {'next_action': 'finish'},
            'pending_turn_slot_fill': True,
            'pending_action': {'status': 'need_info'},
            'last_expert_result': {'expert': 'action', 'data': {}},
        }, 'answer'),
    ],
)
def test_cs_graph_evaluation_observer_captures_terminal_action(
    final_state, expected_action,
):
    from backend.orchestration.graph.cs_graph_node import (
        _publish_cs_eval_result,
        capture_cs_graph_evaluation,
    )

    captured = []
    with capture_cs_graph_evaluation(captured.append):
        _publish_cs_eval_result(final_state)

    assert captured[0]['next_action'] == expected_action


def test_cs_graph_evaluation_observer_only_reports_explicit_fact_ids():
    from backend.orchestration.graph.cs_graph_node import (
        _publish_cs_eval_result,
        capture_cs_graph_evaluation,
    )

    captured = []
    final_state = {
        'supervisor_decision': {'next_action': 'finish'},
        'last_expert_result': {
            'evidence': [{'doc_id': 'doc-1', 'fact_id': 'kb:doc-1#policy'}],
        },
    }
    with capture_cs_graph_evaluation(captured.append):
        _publish_cs_eval_result(final_state)

    assert captured[0]['fact_ids'] == ['kb:doc-1#policy']
    assert captured[0]['fact_ids_observable'] is True


def test_full_path_verifies_miss_and_weak_clarification_routes(monkeypatch):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)

    def miss_stream(self, *args, **kwargs):
        yield {'event': 'status', 'data': {'node': 'router'}}
        yield {'event': 'done', 'data': {'answer': '这是普通问答'}}

    monkeypatch.setattr(MultiAgentSystem, 'stream_events', miss_stream)
    miss = cs._run_live_full_path([
        Case(id='miss', question='天气如何', module='cs',
             expected={'cs_route': 'miss_non_cs'})
    ])[0]
    assert miss.status == 'pass'
    assert miss.metrics['cs_route_match'] == 1.0
    assert miss.actual['route_observed'] == 'miss_non_cs'

    def weak_stream(self, *args, **kwargs):
        yield {'event': 'status', 'data': {'node': 'router'}}
        yield {'event': 'clarification', 'data': {}}
        yield {'event': 'done', 'data': {'answer': '请补充问题'}}

    monkeypatch.setattr(MultiAgentSystem, 'stream_events', weak_stream)
    weak = cs._run_live_full_path([
        Case(id='weak', question='帮我处理一下', module='cs',
             expected={'cs_route': 'clarify_weak', 'next_action': 'clarify'})
    ])[0]
    assert weak.status == 'pass'
    assert weak.metrics['cs_route_match'] == 1.0
    assert weak.metrics['next_action_match'] == 1.0


def test_full_path_rejects_miss_route_that_enters_customer_service(monkeypatch):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)
    monkeypatch.setattr(
        MultiAgentSystem, 'stream_events',
        lambda *args, **kwargs: iter([
            {'event': 'status', 'data': {'node': 'cs_graph_node'}},
            {'event': 'done', 'data': {'answer': '回答'}},
        ]),
    )
    result = cs._run_live_full_path([
        Case(id='wrong-route', question='天气如何', module='cs',
             expected={'cs_route': 'miss_non_cs'})
    ])[0]

    assert result.status == 'fail'
    assert result.metrics['cs_route_match'] == 0.0


def test_full_path_uses_done_answer_and_marks_semantic_checks_unverified(monkeypatch):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)
    monkeypatch.setattr(
        MultiAgentSystem, 'stream_events',
        lambda *args, **kwargs: iter([
            {'event': 'delta', 'data': {'content': '中间流式片段'}},
            {'event': 'done', 'data': {
                'answer': 'done 终态答案',
                'sources': [{'doc_id': 'doc-1', 'title': '不可作为事实 ID'}],
            }},
        ]),
    )
    result = cs._run_live_full_path([
        Case(
            id='unverified-facts', question='问题', module='cs',
            expected={
                'must_contain': ['done 终态答案'],
                'allowed_facts': ['kb:doc-1#policy'],
                'forbidden_facts': ['不得编造政策'],
            },
        )
    ])[0]

    assert result.status == 'skip'
    assert result.actual['answer_head'] == 'done 终态答案'
    assert result.actual['source_ids'] == ['doc-1']
    assert result.actual['fact_ids'] is None
    assert len(result.actual['unverified_checks']) == 2


def test_full_path_compares_observed_terminal_action_exactly(monkeypatch):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem
    from backend.orchestration.graph.cs_graph_node import _publish_cs_eval_result

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)

    def stream(self, *args, **kwargs):
        _publish_cs_eval_result({
            'supervisor_decision': {'next_action': 'pending'},
            'pending_action': {'status': 'need_info'},
        })
        yield {'event': 'status', 'data': {'node': 'cs_graph_node'}}
        yield {'event': 'done', 'data': {'answer': '请补充订单号'}}

    monkeypatch.setattr(MultiAgentSystem, 'stream_events', stream)
    case = Case(
        id='action', question='申请退款', module='cs',
        expected={'cs_route': 'hit', 'next_action': 'propose_with_gap'},
    )
    result = cs._run_live_full_path([case])[0]
    assert result.status == 'pass'
    assert result.actual['next_action'] == 'propose_with_gap'
    assert result.metrics['next_action_match'] == 1.0

    wrong_case = case.model_copy(
        update={'expected': {'cs_route': 'hit', 'next_action': 'propose'}},
    )
    wrong = cs._run_live_full_path([wrong_case])[0]
    assert wrong.status == 'fail'
    assert wrong.metrics['next_action_match'] == 0.0


@pytest.mark.parametrize(
    ('current_pending', 'expected_action'),
    [
        ({
            'action_id': 'new-action', 'proposal_text': '退款申请',
            'confirmation_state': 'pending',
        }, 'propose'),
        ({'status': 'need_info'}, 'propose_with_gap'),
        (None, 'answer'),
    ],
)
def test_full_path_maps_slot_fill_from_current_action_result(
    monkeypatch, current_pending, expected_action,
):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem
    from backend.orchestration.graph.cs_graph_node import _publish_cs_eval_result

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)

    def stream(self, *args, **kwargs):
        result_data = (
            {'pending_action': current_pending}
            if current_pending is not None else {}
        )
        _publish_cs_eval_result({
            'supervisor_decision': {'next_action': 'finish'},
            'pending_turn_slot_fill': True,
            # 模拟图状态保留的旧 need_info 快照；当前轮专家无新 pending 时
            # 不能因此误报仍在补槽。
            'pending_action': {'status': 'need_info'},
            'last_expert_result': {'expert': 'action', 'data': result_data},
        })
        yield {'event': 'status', 'data': {'node': 'cs_graph_node'}}
        yield {'event': 'done', 'data': {'answer': '本轮客服回复'}}

    monkeypatch.setattr(MultiAgentSystem, 'stream_events', stream)
    result = cs._run_live_full_path([
        Case(
            id=f'slot-fill-{expected_action}', question='补充订单号', module='cs',
            expected={'cs_route': 'hit', 'next_action': expected_action},
        )
    ])[0]

    assert result.status == 'pass'
    assert result.actual['next_action'] == expected_action


@pytest.mark.parametrize(
    ('fact_id', 'expected_status'),
    [
        ('kb:approved#policy', 'pass'),
        ('kb:unexpected#policy', 'fail'),
    ],
)
def test_full_path_checks_only_explicit_fact_ids_against_allowlist(
    monkeypatch, fact_id, expected_status,
):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem
    from backend.orchestration.graph.cs_graph_node import _publish_cs_eval_result

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)

    def stream(self, *args, **kwargs):
        _publish_cs_eval_result({
            'supervisor_decision': {'next_action': 'finish'},
            'last_expert_result': {
                'evidence': [{'fact_id': fact_id}],
            },
        })
        yield {'event': 'status', 'data': {'node': 'cs_graph_node'}}
        yield {'event': 'done', 'data': {'answer': '依据已确认信息回复'}}

    monkeypatch.setattr(MultiAgentSystem, 'stream_events', stream)
    result = cs._run_live_full_path([
        Case(
            id='fact-allowlist', question='政策是什么', module='cs',
            expected={
                'cs_route': 'hit',
                'next_action': 'answer',
                'allowed_facts': ['kb:approved#policy'],
            },
        )
    ])[0]

    assert result.status == expected_status
    if expected_status == 'pass':
        assert result.metrics['allowed_facts_verified'] == 1.0
    else:
        assert result.metrics['allowed_facts_verified'] == 0.0


def test_full_path_does_not_pass_without_terminal_action_evidence(monkeypatch):
    from backend.evaluation.models import TestCase as Case
    from backend.evaluation.runners import cs
    from backend.orchestration.graph import MultiAgentSystem

    monkeypatch.setattr(MultiAgentSystem, '__init__', lambda self: None)
    monkeypatch.setattr(
        MultiAgentSystem, 'stream_events',
        lambda *args, **kwargs: iter([
            {'event': 'status', 'data': {'node': 'cs_graph_node'}},
            {'event': 'done', 'data': {'answer': '已有回答'}},
        ]),
    )
    result = cs._run_live_full_path([
        Case(
            id='missing-action', question='问题', module='cs',
            expected={'cs_route': 'hit', 'next_action': 'answer'},
        )
    ])[0]

    assert result.status == 'skip'
    assert result.actual['next_action'] is None
    assert 'next_action 缺少客服终态观察证据' in result.error_msg
