"""内部RAG批次预算的身份与版本边界，不调用付费模型。"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.infra.llm.budget import (
    RequestBudgetExceeded, check_quota_blocked, current_run_budget, scoped_run_budget,
)
from backend.rag.client import RAGServiceProxy


def _request(run_id, limit='10', token='test-internal'):
    headers = {
        'X-Agent-Budget-Run-Id': run_id,
        'X-Agent-Budget-Run-Limit-Cny': limit,
        'X-Internal-Token': token,
    }
    return Request({'type': 'http', 'method': 'POST', 'path': '/ask',
                    'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


@pytest.mark.parametrize('version', [None, True, 1])
def test_client_checks_capability_before_paid_ask(monkeypatch, version):
    from backend.config import messaging

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', 'test-internal')
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == '/readyz':
            return httpx.Response(200, json={'ready': True, 'run_budget_version': version})
        return httpx.Response(200, json={'answer': '回答', 'meta': {}})

    proxy = RAGServiceProxy('http://rag.test', transport=httpx.MockTransport(handler))
    try:
        with scoped_run_budget('eval:transport-test', Decimal('10')):
            if type(version) is not int:
                with pytest.raises(RequestBudgetExceeded, match='remote_unsupported'):
                    proxy.ask('问题', user_id='actor', tenant_id='tenant')
                with pytest.raises(RequestBudgetExceeded):
                    check_quota_blocked()
                assert len(requests) == 1
            else:
                assert proxy.ask('问题', user_id='actor', tenant_id='tenant') == '回答'
                assert requests[-1].headers['X-Agent-Budget-Run-Id'] == 'eval:transport-test'
                assert requests[-1].headers['X-Agent-Budget-Run-Limit-Cny'] == '10'
                assert requests[-1].headers['X-Internal-Token'] == 'test-internal'
    finally:
        proxy._client.close()
    assert current_run_budget() is None


@pytest.mark.parametrize('token,limit,run_id,status', [
    ('wrong', '10', 'eval:sample', 403),
    ('test-internal', '11', 'eval:sample', 422),
    ('test-internal', 'NaN', 'eval:sample', 422),
    ('test-internal', '10', '../sample', 422),
])
def test_server_rejects_forged_or_invalid_run_scope(monkeypatch, token, limit, run_id, status):
    from backend.services import rag_server

    monkeypatch.setattr(rag_server, '_internal_token', lambda: 'test-internal')
    with pytest.raises(HTTPException) as exc:
        rag_server._trusted_run_budget(_request(run_id, limit, token), 'actor')
    assert exc.value.status_code == status


def test_server_binds_run_scope_for_pipeline_and_restores(monkeypatch):
    from backend.config import llm
    from backend.observability.llm_usage_store import current_usage_attribution
    from backend.services import rag_server

    monkeypatch.setattr(rag_server, '_internal_token', lambda: 'test-internal')
    monkeypatch.setattr(llm, 'LLM_BUDGET_MODE', 'enforce')
    run_id = f'eval:transport-{uuid4().hex}'
    observed = []

    def ask(**kwargs):
        observed.append((current_run_budget(), current_usage_attribution()['request_id']))
        return '完成'

    pipeline = SimpleNamespace(ask=ask, last_answer_meta={})
    monkeypatch.setattr(rag_server, '_get_pipeline', lambda: pipeline)
    response = rag_server.ask(
        rag_server.AskRequest(question='问题', user_id='actor', tenant_id='tenant'),
        request=_request(run_id),
    )
    assert response['answer'] == '完成'
    assert observed == [((run_id, Decimal('10')), run_id)]
    assert current_run_budget() is None


def test_remote_run_cap_rejection_marks_parent_scope_blocked(monkeypatch):
    from backend.config import messaging
    from backend.infra.llm.quota import QuotaExceeded

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', 'test-internal')

    def handler(request):
        if request.url.path == '/readyz':
            return httpx.Response(200, json={'run_budget_version': 1})
        return httpx.Response(402, headers={'X-Agent-Quota-Scope': 'request'}, json={})

    proxy = RAGServiceProxy('http://rag.test', transport=httpx.MockTransport(handler))
    try:
        with scoped_run_budget('eval:blocked-run', Decimal('10')):
            with pytest.raises(QuotaExceeded):
                proxy.ask('问题', user_id='actor', tenant_id='tenant')
            with pytest.raises(QuotaExceeded):
                check_quota_blocked()
    finally:
        proxy._client.close()


def test_missing_token_stops_before_any_remote_request(monkeypatch):
    from backend.config import messaging

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', '')
    proxy = RAGServiceProxy('http://rag.test', transport=httpx.MockTransport(
        lambda request: pytest.fail('令牌缺失时不能发起远端请求'),
    ))
    try:
        with scoped_run_budget('eval:no-token', Decimal('10')):
            with pytest.raises(RequestBudgetExceeded, match='internal_token_missing'):
                proxy.ask('问题', user_id='actor', tenant_id='tenant')
    finally:
        proxy._client.close()


def test_development_without_token_cannot_claim_run_scope(monkeypatch):
    from backend.services import rag_server

    monkeypatch.setattr(rag_server, '_internal_token', lambda: '')
    monkeypatch.setattr(rag_server, '_get_pipeline', lambda: pytest.fail('不能初始化pipeline'))
    with pytest.raises(HTTPException) as exc:
        rag_server.ask(
            rag_server.AskRequest(question='问题', user_id='actor', tenant_id='tenant'),
            request=_request('eval:unauthorized'),
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize('call', ['ask', 'retrieve_knowledge', 'retrieve_documents'])
def test_normal_requests_carry_internal_token_when_configured(monkeypatch, call):
    """共享 RAG 开启令牌后，普通问答/检索与评测批次必须能同服务共存。"""
    from backend.config import messaging

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', 'test-internal')
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            'answer': '回答', 'meta': {}, 'result': '片段', 'docs': [],
        })

    proxy = RAGServiceProxy('http://rag.test', transport=httpx.MockTransport(handler))
    try:
        if call == 'ask':
            proxy.ask('问题', user_id='actor', tenant_id='tenant')
        elif call == 'retrieve_knowledge':
            proxy.retrieve_knowledge('问题', tenant_id='tenant')
        else:
            proxy.retrieve_documents('问题', tenant_id='tenant')
    finally:
        proxy._client.close()
    assert seen[0].headers['X-Internal-Token'] == 'test-internal'


def test_normal_ask_without_token_keeps_legacy_behavior(monkeypatch):
    from backend.config import messaging

    monkeypatch.setattr(messaging, 'AI_INTERNAL_TOKEN', '')
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'answer': '回答', 'meta': {}})

    proxy = RAGServiceProxy('http://rag.test', transport=httpx.MockTransport(handler))
    try:
        proxy.ask('问题', user_id='actor', tenant_id='tenant')
    finally:
        proxy._client.close()
    assert 'X-Internal-Token' not in seen[0].headers
