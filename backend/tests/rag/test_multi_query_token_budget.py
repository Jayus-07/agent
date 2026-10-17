"""多查询改写的 token 预算与角色解析（2026-10-09 截断缺陷回归）。

缺陷背景（trace_store 实测，154 次改写样本）：
  completion_tokens 撞上 MULTI_QUERY_MAX_TOKENS=200 的调用有 151 次，
  且全部只解析出 1 个变体；而未撞上限的 3 次（145/155/191 tokens）
  全部正常产出 3 个变体 —— 98% 的调用被 max_tokens 截断。
根因：主模型为思考型（doubao-seed-2.0-mini），reasoning 计入
completion_tokens，思考吃掉大部分预算后正文尚未写完即被截断，
多查询因此长期静默退化为单查询（仍白付 ~2.3s 串行等待）。

本组测试锁定 token 上限、统一 Proxy 入口及失败降级，避免回归：
  1. 改写 token 上限不得低于安全下限（否则思考型模型必然截断）；
  2. 改写走 multi_query 角色和统一 Proxy；角色不可用/预算拒绝时退回原始 query。
"""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest


def test_rewrite_max_tokens_never_below_floor(monkeypatch):
    """配置值低于安全下限时必须被抬到下限（截断缺陷的直接护栏）。"""
    import backend.rag.retrieval.multi_query as mq

    monkeypatch.setattr(mq, "MULTI_QUERY_MAX_TOKENS", 200)
    assert mq._rewrite_max_tokens() == mq._REWRITE_MAX_TOKENS_FLOOR
    assert mq._REWRITE_MAX_TOKENS_FLOOR >= 512


def test_rewrite_max_tokens_respects_larger_config(monkeypatch):
    """管理员显式配了更大的值时不得被下限压低。"""
    import backend.rag.retrieval.multi_query as mq

    monkeypatch.setattr(mq, "MULTI_QUERY_MAX_TOKENS", 4096)
    assert mq._rewrite_max_tokens() == 4096


def test_rewrite_max_tokens_tolerates_bad_config(monkeypatch):
    """非法配置值不应翻异常（检索前置步骤必须 fail-open）。"""
    import backend.rag.retrieval.multi_query as mq

    monkeypatch.setattr(mq, "MULTI_QUERY_MAX_TOKENS", "not-a-number")
    assert mq._rewrite_max_tokens() == mq._REWRITE_MAX_TOKENS_FLOOR


def test_rewrite_llm_uses_multi_query_role(monkeypatch):
    """改写通过治理入口绑定角色、确定温度和 token 上限。"""
    import backend.rag.retrieval.multi_query as mq

    sentinel = MagicMock(name="role-llm")
    captured = {}

    def bind(role, *, temperature=None, max_tokens=None):
        captured.update(
            role=role, temperature=temperature, max_tokens=max_tokens,
        )
        return sentinel

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", bind)
    monkeypatch.setattr(mq, "MULTI_QUERY_MAX_TOKENS", 200)

    assert mq._resolve_rewrite_llm() is sentinel
    assert captured == {
        "role": "multi_query",
        "temperature": 0,
        "max_tokens": mq._REWRITE_MAX_TOKENS_FLOOR,
    }


def test_rewrite_calls_governed_proxy_once_and_parses_variants(monkeypatch):
    """成功改写只发起一次角色 Proxy 调用，并保留原查询作检索首项。"""
    import backend.rag.retrieval.multi_query as mq

    question = "比较退款与退货的处理差异"
    proxy = MagicMock(name="governed-proxy")
    proxy.invoke.return_value = type(
        "Response", (), {
            "content": "退款申请的审核与处理步骤\n退货申请的审核与处理步骤",
        },
    )()
    binder = MagicMock(return_value=proxy)
    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", binder)
    monkeypatch.setattr(
        mq.prompt_service,
        "render_sync",
        lambda *_a, **_k: type("Rendered", (), {"text": "rewrite"})(),
    )

    rewritten = mq._rewrite(question)

    binder.assert_called_once_with(
        "multi_query", temperature=0, max_tokens=mq._rewrite_max_tokens(),
    )
    proxy.invoke.assert_called_once()
    assert rewritten[0] == question
    assert len(rewritten) > 1


def test_rewrite_falls_back_to_original_query_when_role_unavailable(
    monkeypatch, caplog,
):
    """角色不可用时跳过改写，绝不调用裸全局 LLM 或泄露异常文本。"""
    import backend.rag.retrieval.multi_query as mq

    question = "比较退货和退款的审核流程"
    secret_marker = "provider-secret-marker"
    global_llm = MagicMock(name="global-llm")
    monkeypatch.setattr("backend.infra.llm.llm", global_llm)
    monkeypatch.setattr(
        mq.prompt_service,
        "render_sync",
        lambda *_a, **_k: type("Rendered", (), {"text": "rewrite"})(),
    )
    monkeypatch.setattr(
        "backend.infra.llm.proxy.bind_llm_for_role",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError(f"registry down: {secret_marker}"),
        ),
    )

    assert mq._rewrite(question) == [question]
    global_llm.invoke.assert_not_called()
    assert secret_marker not in caplog.text


@pytest.mark.parametrize(
    "failure_kind", ["budget-rejected", "provider-failure"],
)
def test_rewrite_proxy_failure_falls_back_without_leaking_error(
    monkeypatch, caplog, failure_kind,
):
    """Proxy 的预算拒绝/Provider 异常均保留原始 query，错误内容不进日志。"""
    import backend.rag.retrieval.multi_query as mq
    if failure_kind == "budget-rejected":
        from backend.infra.llm.budget import RequestBudgetExceeded

        failure = RequestBudgetExceeded("request budget exceeded")
    else:
        failure = TimeoutError("provider secret-marker")

    question = "比较退货和退款的审核流程"
    proxy = MagicMock(name="governed-proxy")
    proxy.invoke.side_effect = failure
    monkeypatch.setattr(
        "backend.infra.llm.proxy.bind_llm_for_role", lambda *_a, **_k: proxy,
    )
    monkeypatch.setattr(
        mq.prompt_service,
        "render_sync",
        lambda *_a, **_k: type("Rendered", (), {"text": "rewrite"})(),
    )

    assert mq._rewrite(question) == [question]
    proxy.invoke.assert_called_once()
    assert "secret-marker" not in caplog.text


def test_rewrite_worker_preserves_tenant_user_and_trace(monkeypatch):
    """线程池中的改写任务沿用请求身份和 Trace，保证账本归因一致。"""
    import backend.rag.retrieval.multi_query as mq
    from backend.infra import thread_pools
    from backend.config import rag as rag_config
    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget as budget_mod, proxy as proxy_mod
    from backend.observability.llm_usage_store import current_usage_attribution
    from backend.observability.tracer import trace_collector
    from backend.rag import retrieval_budget

    trace_collector.clear_for_test()
    trace = trace_collector.start(question="MultiQuery attribution")
    executor = ThreadPoolExecutor(max_workers=1)
    seen = []

    def inspect_request_context(question):
        attribution = current_usage_attribution()
        seen.append((
            attribution["tenant_id"],
            attribution["user_id"],
            attribution["trace_id"],
            trace_collector.current().id,
            proxy_mod._thread_local_user_id(),
            budget_mod.current_request_budget().tenant_id,
            budget_mod.current_request_budget().user_id,
        ))
        return [question]

    monkeypatch.setattr(mq, "_rewrite", inspect_request_context)
    monkeypatch.setattr(
        thread_pools,
        "submit_rag_task",
        lambda _name, fn, *args, **kwargs: executor.submit(fn, *args, **kwargs),
    )
    monkeypatch.setattr(retrieval_budget, "remaining_ms", lambda limit: limit)
    monkeypatch.setattr(rag_config, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 1000)
    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: object(),
    )
    user_token = proxy_mod._user_id_var.set("mq-user")
    try:
        with scoped_tool_identity(user_id="mq-user", tenant_id="mq-tenant"):
            with budget_mod.scoped_request_budget(
                trace.id, user_id="mq-user", tenant_id="mq-tenant",
            ):
                assert mq._rewrite_with_budget("复杂查询") == ["复杂查询"]
    finally:
        proxy_mod._user_id_var.reset(user_token)
        executor.shutdown(wait=True)
        trace_collector.clear_for_test()

    assert seen == [(
        "mq-tenant", "mq-user", trace.id, trace.id,
        "mq-user", "mq-tenant", "mq-user",
    )]


def test_rewrite_timeout_obeys_multi_query_role_policy(monkeypatch):
    """角色策略的较短 timeout 必须先于模块配置收口改写等待。"""
    import backend.rag.retrieval.multi_query as mq
    from backend.config import model_roles, rag as rag_config
    from backend.infra import thread_pools
    from backend.rag import retrieval_budget

    captured = {}

    class _Future:
        def result(self, timeout):
            captured["timeout"] = timeout
            return ["rewritten"]

    class _Pool:
        def submit(self, *_args, **_kwargs):
            return _Future()

    monkeypatch.setattr(
        model_roles,
        "resolve_runtime_policy",
        lambda _role: model_roles.RoleRuntimeDefaults(
            timeout_seconds=1, max_retries=0, failure_policy="skip",
        ),
    )
    monkeypatch.setattr(rag_config, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 5000)
    monkeypatch.setattr(retrieval_budget, "remaining_ms", lambda limit: limit)
    monkeypatch.setattr(
        thread_pools,
        "submit_rag_task",
        lambda _name, fn, *args, **kwargs: _Pool().submit(fn, *args, **kwargs),
    )

    assert mq._rewrite_with_budget("复杂查询") == ["rewritten"]
    assert captured["timeout"] == 1.0


def test_rewrite_timeout_cancels_queued_work_and_returns_original(monkeypatch):
    """等待超时会取消尚未启动的线程池任务，避免过期查询再触发 LLM。"""
    from concurrent.futures import TimeoutError

    import backend.rag.retrieval.multi_query as mq
    from backend.config import model_roles, rag as rag_config
    from backend.infra import thread_pools
    from backend.rag import retrieval_budget
    from backend.observability.tracer import trace_collector

    state = {"cancelled": False}

    class _Future:
        def result(self, timeout):
            raise TimeoutError

        def cancel(self):
            state["cancelled"] = True
            return True

    class _Pool:
        def submit(self, _function, *args, **_kwargs):
            state["cancel_event"] = args[1]
            return _Future()

    monkeypatch.setattr(
        model_roles,
        "resolve_runtime_policy",
        lambda _role: model_roles.RoleRuntimeDefaults(
            timeout_seconds=1, max_retries=0, failure_policy="skip",
        ),
    )
    monkeypatch.setattr(rag_config, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 500)
    monkeypatch.setattr(retrieval_budget, "remaining_ms", lambda limit: limit)
    monkeypatch.setattr(
        thread_pools,
        "submit_rag_task",
        lambda _name, fn, *args, **kwargs: _Pool().submit(fn, *args, **kwargs),
    )
    monkeypatch.setattr(trace_collector, "end_open_span", lambda *_a, **_k: None)

    question = "复杂查询"
    assert mq._rewrite_with_budget(question) == [question]
    assert state["cancelled"] is True
    assert state["cancel_event"].is_set()


def test_multi_query_role_registered_and_inherits_main():
    """角色必须已登记、继承 main（保证未配置时行为与旧版一致）。"""
    from backend.config import model_roles
    from backend.config.model_roles import MODEL_ROLES, runtime_defaults

    spec = MODEL_ROLES["multi_query"]
    assert spec.inherit == "main"
    assert spec.env_key == "MULTI_QUERY_MODEL"
    assert model_roles.policy_enforced("multi_query") is True
    policy = runtime_defaults("multi_query")
    # 改写失败轻量降级为单查询，绝不阻塞主检索链
    assert policy.failure_policy == "skip"
