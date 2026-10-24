from backend.evaluation_v2 import repository


class _Cursor:
    def __init__(self):
        self.statement = ""

    def execute(self, statement, _params=None):
        self.statement = " ".join(statement.split())

    def fetchall(self):
        return [{
            "run_id": "run-1",
            "cost_amount": None,
            "cost_currency": None,
        }]


def test_run_listing_exposes_null_cost_without_inventing_zero(monkeypatch):
    from contextlib import contextmanager

    cursor = _Cursor()

    @contextmanager
    def transaction(_tenant_id):
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)
    items = repository.list_runs("tenant-a")

    assert "r.cost_amount, r.cost_currency" in cursor.statement
    assert items == [{"run_id": "run-1", "cost_amount": None, "cost_currency": None}]
