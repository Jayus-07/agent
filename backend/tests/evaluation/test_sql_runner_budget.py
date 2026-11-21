# -*- coding: utf-8 -*-
"""_run_live 逐用例请求预算隔离回归（2026-11-21 sql_v7 在线评测实测）。

修复前：评测调用不带 request_id，全部用例共享空串预算状态——累计
超限后同一 run 内剩余用例集体秒拒（22 条同报一个累计值），评测结果
被预算污染无法区分真实质量。

回归锁定：每条用例在独立的 scoped_request_budget 下执行（request_id
互不相同、格式 eval-sql-{case.id}），退出后上下文还原。
"""
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.runners.sql import _run_live
from backend.infra.llm import budget as llm_budget
from backend.sql.sql_result import SQLResult


def _make_case(case_id: str, *, should_reject: bool = False) -> TestCase:
    expected = (
        {"should_reject": True, "expected_status": ["no_table"]}
        if should_reject
        else {"gold_sql": None, "expected_tables": ["product.products"]}
    )
    return TestCase(id=case_id, module="sql", question="q", expected=expected)


def test_run_live_isolates_request_budget_per_case(monkeypatch):
    seen_ids: list[str] = []
    cases = [_make_case("SC-001"), _make_case("SC-002", should_reject=True)]

    class _FakeAgent:
        def ask_struct(self, question, **kwargs):
            seen_ids.append(llm_budget._current_request_id.get())
            if cases[1].expected["should_reject"] and len(seen_ids) == 2:
                return SQLResult.failed("no_table", "已被路由拒绝")
            return SQLResult.success(
                rows=[{"id": 1}], columns=["id"],
                sql="SELECT id FROM product.products")

    monkeypatch.setattr(
        "backend.sql.sql_agent.get_sql_agent", lambda: _FakeAgent())

    results = _run_live(cases)

    # 每条用例拿到互不相同、可追溯的 request_id
    assert seen_ids == ["eval-sql-SC-001", "eval-sql-SC-002"]
    assert len(results) == 2
    assert all(r.status in ("pass", "fail") for r in results)
    # scoped 退出后上下文还原，不把评测的 request_id 泄漏给后续调用
    assert llm_budget._current_request_id.get() == ""


def test_run_live_budget_reset_between_cases(monkeypatch):
    """前一条用例耗尽预算不得污染后一条：第二条仍能真实调用。"""
    calls: list[str] = []
    cases = [_make_case("SC-001"), _make_case("SC-002")]

    class _FakeAgent:
        def ask_struct(self, question, **kwargs):
            calls.append(llm_budget._current_request_id.get())
            return SQLResult.success(
                rows=[], columns=["id"], sql="SELECT id FROM product.products")

    monkeypatch.setattr(
        "backend.sql.sql_agent.get_sql_agent", lambda: _FakeAgent())

    results = _run_live(cases)
    assert len(calls) == 2 and calls[0] != calls[1]
    assert len(results) == 2
