import io
import json
import os

import pytest

from scripts import run_cs_live


def test_host_environment_uses_remote_and_disables_refund(monkeypatch):
    for key in (
        'RAG_MODE', 'RAG_SERVICE_URL', 'NO_PROXY',
        'CS_REFUND_REQUEST_MODE', 'BUSINESS_SERVICE_URL',
    ):
        monkeypatch.setenv(key, 'old')
    run_cs_live.prepare_environment()
    import os

    assert os.environ['BUSINESS_SERVICE_URL'] == 'http://127.0.0.1:8081'
    assert os.environ['RAG_MODE'] == 'remote'
    assert os.environ['RAG_SERVICE_URL'] == 'http://127.0.0.1:8090'
    assert os.environ['NO_PROXY'] == '*'
    assert os.environ['CS_REFUND_REQUEST_MODE'] == 'disabled'


def test_host_entrypoint_accepts_isolated_loopback_ports(monkeypatch):
    monkeypatch.setenv('CS_EVAL_RAG_URL', 'http://127.0.0.1:18090')
    monkeypatch.setenv('CS_EVAL_BUSINESS_URL', 'http://127.0.0.1:18081')
    for key in ('RAG_MODE', 'RAG_SERVICE_URL', 'BUSINESS_SERVICE_URL',
                'NO_PROXY', 'CS_REFUND_REQUEST_MODE'):
        monkeypatch.setenv(key, 'old')
    run_cs_live.prepare_environment()
    assert os.environ['RAG_SERVICE_URL'] == 'http://127.0.0.1:18090'
    assert os.environ['BUSINESS_SERVICE_URL'] == 'http://127.0.0.1:18081'


@pytest.mark.parametrize('value', ['https://outside.test', 'http://127.0.0.1@outside.test'])
def test_host_entrypoint_rejects_nonlocal_service_url(monkeypatch, value):
    monkeypatch.setenv('CS_EVAL_RAG_URL', value)
    with pytest.raises(ValueError, match='本机'):
        run_cs_live.prepare_environment()


def test_preflight_stops_without_token_before_http(monkeypatch):
    from backend.config import messaging

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', '')
    monkeypatch.setattr(run_cs_live, 'build_opener', lambda *args: pytest.fail('不能请求HTTP'))
    with pytest.raises(RuntimeError, match='内部令牌'):
        run_cs_live.check_services()


@pytest.mark.parametrize('ready', [True, False])
def test_preflight_requires_ready_rag_and_models(monkeypatch, ready):
    from backend.config import llm, messaging

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(llm, 'LLM_BUDGET_MODE', 'enforce')
    monkeypatch.setenv('RAG_SERVICE_URL', 'http://127.0.0.1:18090')
    monkeypatch.setenv('BUSINESS_SERVICE_URL', 'http://127.0.0.1:18081')
    responses = [
        {'status': 'ok', 'models_loaded': ['embedder', 'reranker']},
        {'ready': ready, 'run_budget_version': 1},
    ]

    class Opener:
        def open(self, url, timeout):
            if url.endswith('/health'):
                response = io.BytesIO()
                response.status = 200
                return response
            return io.BytesIO(json.dumps(responses.pop(0)).encode())

    monkeypatch.setattr(run_cs_live, 'build_opener', lambda *args: Opener())
    if ready:
        run_cs_live.check_services()
    else:
        with pytest.raises(RuntimeError, match='RAG'):
            run_cs_live.check_services()


def test_unready_services_stop_before_registry_or_evaluation(monkeypatch):
    def unavailable():
        raise RuntimeError('尚未就绪')

    monkeypatch.setattr(run_cs_live, 'check_services', unavailable)
    monkeypatch.setattr(run_cs_live, 'prepare_environment', lambda: None)
    with pytest.raises(RuntimeError, match='尚未就绪'):
        run_cs_live.main()


def test_launch_selection_keeps_v1_and_all_six_cs2_categories():
    from collections import Counter

    from backend.evaluation.dataset import load_dataset

    selected = load_dataset('cs', selection='cs_public_launch_smoke')
    canonical = {case.id: case for case in load_dataset('cs')}
    assert len({case.id for case in selected}) == 60
    assert {case.id for case in selected if case.id.startswith('CS-')} == {
        f'CS-{index:03d}' for index in range(1, 21)
    }
    categories = Counter(
        case.metadata.get('category') for case in selected
        if case.id.startswith('CS2-')
    )
    assert categories == {
        'faq': 12, 'query': 8, 'action': 8,
        'complaint': 4, 'safety': 4, 'multi_turn': 4,
    }
    for case in selected:
        assert case.question == canonical[case.id].question
        assert case.expected == canonical[case.id].expected
