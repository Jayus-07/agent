"""L5 Golden 评测会话写入必须遵守产品作用域唯一键。"""

import pytest

from backend.evaluation.context_budget import golden_eval


class _Cursor:
    def __init__(self, *, conflict=False):
        self.calls = []
        self.conflict = conflict

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchone(self):
        if self.conflict and len(self.calls) == 1:
            return None
        return (len(self.calls),)


class _Connection:
    def __init__(self, *, conflict=False):
        self.cursor_instance = _Cursor(conflict=conflict)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _Engine:
    def __init__(self, *, conflict=False):
        self.connection = _Connection(conflict=conflict)

    def raw_connection(self):
        return self.connection


def test_seed_session_uses_product_scoped_schema(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(
        "backend.infra.db.get_memory_engine", lambda: engine,
    )

    ids = golden_eval._seed_session(
        "eval-golden-test",
        [
            {"role": "user", "content": "合成测试输入"},
            {"role": "assistant", "content": "合成测试回复"},
        ],
    )

    calls = engine.connection.cursor_instance.calls
    assert ids == [2, 3]
    assert "ON CONFLICT DO NOTHING" in calls[0][0]
    assert calls[0][1] == (
        "eval-golden-test", "p4-golden", "agent", "default",
    )
    assert all("product, role, content" in query for query, _ in calls[1:])
    assert all(params[1] == "agent" for _, params in calls[1:])
    assert engine.connection.committed
    assert engine.connection.closed


def test_seed_session_stops_before_messages_on_id_collision(monkeypatch):
    engine = _Engine(conflict=True)
    monkeypatch.setattr(
        "backend.infra.db.get_memory_engine", lambda: engine,
    )

    with pytest.raises(RuntimeError, match="ID 冲突"):
        golden_eval._seed_session("eval-golden-collision", [])

    assert len(engine.connection.cursor_instance.calls) == 1
    assert engine.connection.rolled_back
    assert engine.connection.closed


def test_golden_model_calls_fail_closed_without_enforce(monkeypatch):
    monkeypatch.setattr("backend.config.llm.LLM_BUDGET_MODE", "observe")

    with pytest.raises(RuntimeError, match="LLM_BUDGET_MODE=enforce"):
        with golden_eval._golden_request_scope("golden-budget-off"):
            pass


def test_golden_model_calls_share_an_enforced_request_budget(monkeypatch):
    from backend.infra.llm.budget import current_request_budget

    monkeypatch.setattr("backend.config.llm.LLM_BUDGET_MODE", "enforce")

    assert current_request_budget() is None
    with golden_eval._golden_request_scope("golden-budget-test"):
        budget = current_request_budget()
        assert budget is not None
        assert budget.mode == "enforce"
        assert budget.quota_store is None
    assert current_request_budget() is None
