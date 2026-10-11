"""PR Smoke required gate and Billing V2 artifact contracts."""
from pathlib import Path


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
    assert "BASE_SHA\" || -z \"$HEAD_SHA" in smoke
    assert "git diff --name-only \"$BASE_SHA\" \"$HEAD_SHA\" > \"$changed_paths\"" in smoke
    assert "python3 .github/scripts/rag_smoke_path_filter.py < \"$changed_paths\"" in smoke

    # Both workflows summarize the Billing V2 base-currency field (CNY), not
    # the deprecated compatibility amount whose currency can be mixed.
    for workflow in (smoke, regression):
        assert "sum(coalesce(billed_cost_cny, 0))" in workflow
        assert '"billed_cost_cny": round(float(r[4] or 0), 6)' in workflow
        assert "sum(coalesce(cost_usd, 0))" not in workflow
        assert '"cost_usd": round(float(r[4] or 0), 6)' not in workflow

    # The regression fee-artifact step uses pathlib.Path before writing output.
    assert "from pathlib import Path" in regression
