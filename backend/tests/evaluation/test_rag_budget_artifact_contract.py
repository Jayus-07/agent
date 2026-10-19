"""可信 RAG 评测必须在预算门禁下运行并导出请求级对账证据。"""
import json
from pathlib import Path
from types import SimpleNamespace


def test_rag_smoke_enforces_and_reports_budget_ledger():
    repository_root = Path(__file__).resolve().parents[3]
    workflow = (repository_root / ".github/workflows/rag_smoke.yml").read_text(
        encoding="utf-8",
    )

    assert "LLM_BUDGET_MODE: enforce" in workflow
    assert (
        "EVAL_RUN_ID: rag-smoke-${{ github.run_id }}-${{ github.run_attempt }}"
        in workflow
    )
    assert '--run-id "$EVAL_RUN_ID"' in workflow
    assert 'WHERE request_id = %s' in workflow
    assert "budget_ledger_verification" in workflow
    assert "unattributed_calls" in workflow
    assert "reservation_total == usage_calls" in workflow


def test_rag_smoke_budget_artifact_reports_reconciled_simulated_ledger(
    monkeypatch, tmp_path,
):
    import sys
    from decimal import Decimal

    import yaml

    repository_root = Path(__file__).resolve().parents[3]
    definition = yaml.safe_load(
        (repository_root / ".github/workflows/rag_smoke.yml").read_text(
            encoding="utf-8",
        ),
    )
    steps = definition["jobs"]["rag-smoke"]["steps"]
    run = next(
        step["run"] for step in steps
        if step.get("name") == "Collect API cost attribution"
    )
    script = run.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    class _Cursor:
        def __init__(self):
            self._rows = []
            self._one = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, _params=None):
            if "GROUP BY component, model" in query:
                self._rows = [(
                    "llm", "test-model", "test-provider", "exact",
                    "approved_price_table", 2, 100, Decimal("0.012000"),
                )]
            elif "count(*) FILTER" in query:
                self._one = (2, 0, 100, Decimal("0.012000"))
            elif "reservation_per_call" in query:
                self._rows = [(
                    "settled", 2, Decimal("1.000000"), Decimal("0.012000"),
                )]
            else:
                raise AssertionError("测试遇到未覆盖的账本查询")

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._one

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda **_kwargs: _Connection()),
    )
    monkeypatch.setenv("MEMORY_PGDATABASE", "test_db")
    monkeypatch.setenv("EVAL_TRIGGERED_BY", "github-actions-rag-smoke")
    monkeypatch.setenv("EVAL_RUN_ID", "rag-smoke-test-run")
    monkeypatch.setenv("LLM_BUDGET_MODE", "enforce")
    monkeypatch.setenv("LLM_USAGE_START_ID", "12")
    monkeypatch.chdir(tmp_path)

    exec(compile(script, "collect_api_cost_attribution", "exec"), {})

    artifact = json.loads(
        (tmp_path / "data/eval_runs/llm_usage_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert artifact["eval_run_id"] == "rag-smoke-test-run"
    assert artifact["budget_mode"] == "enforce"
    assert artifact["budget_ledger_verification"] == "VERIFIED"
    assert artifact["usage_calls"] == 2
    assert artifact["unattributed_calls"] == 0
    assert artifact["budget_reservations"] == {"settled": 2}
    assert artifact["reservation_amounts_cny"] == {
        "settled": {"reserved_cny": 1.0, "settled_cny": 0.012},
    }
    assert artifact["ledger_difference_cny"] == 0.0
