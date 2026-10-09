"""多查询改写的 token 预算与角色解析（2026-10-09 截断缺陷回归）。

缺陷背景（trace_store 实测，154 次改写样本）：
  completion_tokens 撞上 MULTI_QUERY_MAX_TOKENS=200 的调用有 151 次，
  且全部只解析出 1 个变体；而未撞上限的 3 次（145/155/191 tokens）
  全部正常产出 3 个变体 —— 98% 的调用被 max_tokens 截断。
根因：主模型为思考型（doubao-seed-2.0-mini），reasoning 计入
completion_tokens，思考吃掉大部分预算后正文尚未写完即被截断，
多查询因此长期静默退化为单查询（仍白付 ~2.3s 串行等待）。

本组测试锁两件事，避免回归：
  1. 改写 token 上限不得低于安全下限（否则思考型模型必然截断）；
  2. 改写走 multi_query 角色，角色不可用时回落主链 LLM（fail-open）。
"""
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
    """改写应经 multi_query 角色解析，而非固定走主链 LLM。"""
    import backend.config.model_roles as model_roles
    import backend.rag.retrieval.multi_query as mq

    sentinel = MagicMock(name="role-llm")
    monkeypatch.setattr(
        model_roles, "resolve_runtime_name",
        lambda role, *a, **k: "qwen3.8-flash" if role == "multi_query" else "",
    )
    monkeypatch.setattr(
        "backend.infra.llm.proxy.get_llm_for_role",
        lambda role: sentinel if role == "multi_query" else None,
    )
    assert mq._resolve_rewrite_llm() is sentinel


def test_rewrite_llm_falls_back_when_role_unavailable(monkeypatch):
    """角色解析失败必须回落主链 LLM —— 可用性优先于角色正确性。"""
    import backend.config.model_roles as model_roles
    import backend.rag.retrieval.multi_query as mq

    fallback = MagicMock(name="main-llm")
    monkeypatch.setattr(
        model_roles, "resolve_runtime_name",
        lambda role, *a, **k: (_ for _ in ()).throw(RuntimeError("registry down")),
    )
    monkeypatch.setattr("backend.infra.llm.llm", fallback)
    assert mq._resolve_rewrite_llm() is fallback


def test_multi_query_role_registered_and_inherits_main():
    """角色必须已登记、继承 main（保证未配置时行为与旧版一致）。"""
    from backend.config.model_roles import MODEL_ROLES, runtime_defaults

    spec = MODEL_ROLES["multi_query"]
    assert spec.inherit == "main"
    assert spec.env_key == "MULTI_QUERY_MODEL"
    policy = runtime_defaults("multi_query")
    # 改写失败轻量降级为单查询，绝不阻塞主检索链
    assert policy.failure_policy == "skip"
