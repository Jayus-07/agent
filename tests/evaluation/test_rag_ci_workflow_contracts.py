"""PR Smoke required gate and Billing V2 artifact contracts."""
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def test_rag_required_gate_and_cost_artifacts_follow_ci_contract():
    smoke = (ROOT / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    regression = (ROOT / ".github/workflows/rag_regression.yml").read_text(encoding="utf-8")

    # The stable required check must validate the appropriate lane and cannot
    # treat a skipped relevant RAG run as successful.
    assert "rag-smoke-gate:\n    name: RAG Smoke (8 cases)" in smoke
    assert "needs: [detect-changes, rag-smoke-pr-static, rag-smoke]" in smoke
    assert "if: ${{ always() }}" in smoke
    assert 'PR_PREFLIGHT_RESULT" != "success" || "$SMOKE_RESULT" != "skipped"' in smoke
    assert '"$SMOKE_RESULT" != "success"' in smoke
    assert "RAG Smoke Evaluation (8 cases, real API)" in smoke
    assert "(github.event_name == 'push' || github.event_name == 'workflow_dispatch')" in smoke
    assert "if: github.event_name == 'pull_request' && needs.detect-changes.outputs.relevant == 'true'" in smoke
    assert "github.event.pull_request.base.sha || github.event.before" in smoke
    assert "github.event.pull_request.head.sha || github.sha" in smoke
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" ]]; then' in smoke
    assert 'if [[ "$EVENT_NAME" == "push" && "$BASE_SHA" == "0000000000000000000000000000000000000000" ]]; then' in smoke
    assert "BASE_SHA\" || -z \"$HEAD_SHA" in smoke
    assert "git diff --name-only \"$BASE_SHA\" \"$HEAD_SHA\" > \"$changed_paths\"" in smoke
    assert "python3 .github/scripts/rag_smoke_path_filter.py < \"$changed_paths\"" in smoke
    assert "EVAL_TRIGGER: ${{ github.event_name }}" in smoke
    assert "EVAL_TRIGGER: ${{ github.event_name }}" in regression

    # Both workflows summarize the Billing V2 base-currency field (CNY), not
    # the deprecated compatibility amount whose currency can be mixed.
    for workflow in (smoke, regression):
        assert "sum(coalesce(billed_cost_cny, 0))" in workflow
        assert "GROUP BY model, provider, cost_status, pricing_source" in workflow
        assert '"cost_verification": cost_verification' in workflow
        assert '"unverified_calls": unverified_calls' in workflow
        assert '"pricing_source": r[3] or "unknown"' in workflow
        assert '"cost_status": cost_status or "unknown"' in workflow
        assert '"UNVERIFIED" if not rows or unverified_calls' in workflow
        assert 'else round(float(r[6] or 0), 6)' in workflow
        assert "sum(coalesce(cost_usd, 0))" not in workflow
        assert '"cost_usd": round(float(r[4] or 0), 6)' not in workflow

    # The regression fee-artifact step uses pathlib.Path before writing output.
    assert "from pathlib import Path" in regression


def _cost_attribution_script(workflow: str) -> str:
    step_start = workflow.index("- name: Collect API cost attribution")
    script_start = workflow.index("python - <<'PY'\n", step_start)
    script_start += len("python - <<'PY'\n")
    script_end = workflow.index("\n          PY", script_start)
    return textwrap.dedent(workflow[script_start:script_end])


def test_cost_artifacts_mark_unpriced_calls_unverified(tmp_path, monkeypatch):
    """两条 CI lane 都保留未定价状态，不能把未知成本报告成零元。"""

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _query):
            return None

        def fetchall(self):
            return [
                ("embedding-model", "provider-a", "unpriced", "unpriced", 1, 12, 0),
                (
                    "chat-model",
                    "provider-b",
                    "estimated",
                    "registry_fallback",
                    2,
                    40,
                    0.02,
                ),
            ]

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setenv("MEMORY_PGDATABASE", "isolated-test-db")
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda **_kwargs: _Connection()),
    )

    for workflow_name in ("rag_smoke.yml", "rag_regression.yml"):
        run_dir = tmp_path / workflow_name
        run_dir.mkdir()
        monkeypatch.chdir(run_dir)
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        exec(compile(_cost_attribution_script(workflow), workflow_name, "exec"), {})
        artifact = json.loads(
            (run_dir / "data/eval_runs/llm_usage_summary.json").read_text(
                encoding="utf-8"
            )
        )

        assert artifact["cost_verification"] == "UNVERIFIED"
        assert artifact["unverified_calls"] == 1
        unpriced = next(
            row for row in artifact["usage"] if row["cost_status"] == "unpriced"
        )
        assert unpriced["pricing_source"] == "unpriced"
        assert unpriced["billed_cost_cny"] is None
        estimated = next(
            row for row in artifact["usage"] if row["cost_status"] == "estimated"
        )
        assert estimated["billed_cost_cny"] == 0.02
