"""TD-05 预算旁路可诊断化回归（2026-10-02）。

实测背景：聊天链路部分线程只绑 trace 没绑身份（llm_usage 里带钱的
doubao 行 user/tenant 为空），reserve_model_call 在 state None 时静默
旁路——budget_reservations 零行、管理端预算主体列表不可见，且无任何
日志可查。本文件钉死：
  1. mode=enforce + state 未绑定 → reserve 旁路且打 warning（可诊断）；
  2. mode=off + state 未绑定 → 设计内旁路，不告警；
  3. enforce + 有 user 无 tenant（quota_store None）→ 打 warning 指向
     租户头注入链路。
"""
import logging

import pytest

from backend.infra.llm import budget as budget_mod


@pytest.fixture(autouse=True)
def _clean_budget_state():
    budget_mod._states.clear()
    budget_mod.clear_request_budget()
    yield
    budget_mod._states.clear()
    budget_mod.clear_request_budget()


def test_reserve_bypass_warns_when_enforce(caplog):
    """enforce + 无请求状态 → 旁路 + warning 指向缺失的 bind。"""
    from backend.config import llm as config

    original = config.LLM_BUDGET_MODE
    try:
        config.LLM_BUDGET_MODE = "enforce"
        with caplog.at_level(logging.WARNING, logger="rag_system"):
            budget_mod.reserve_model_call("primary", model_name="m-test")
        assert any("reserve 旁路" in r.message for r in caplog.records)
    finally:
        config.LLM_BUDGET_MODE = original


def test_reserve_bypass_silent_when_off(caplog):
    """mode=off 的旁路是设计内行为，不应告警。"""
    from backend.config import llm as config

    original = config.LLM_BUDGET_MODE
    try:
        config.LLM_BUDGET_MODE = "off"
        with caplog.at_level(logging.WARNING, logger="rag_system"):
            budget_mod.reserve_model_call("primary", model_name="m-test")
        assert not any("reserve 旁路" in r.message for r in caplog.records)
    finally:
        config.LLM_BUDGET_MODE = original


def test_missing_quota_store_warns_with_tenant_hint(caplog):
    """enforce + user 有值 + quota_store None（bind 时 tenant 缺失）→ warning。"""
    from backend.infra.llm.budget import RequestBudget

    state = RequestBudget(mode="enforce", user_id="443", tenant_id="", quota_store=None)
    budget_mod._states["req-test-1"] = state
    budget_mod._current_request_id.set("req-test-1")
    with caplog.at_level(logging.WARNING, logger="rag_system"):
        state.reserve("primary", model_name="m-test")
    assert any("quota_store 缺失" in r.message for r in caplog.records)
    assert state.calls == 1  # 门禁计数照常推进，不因缺 store 中断
