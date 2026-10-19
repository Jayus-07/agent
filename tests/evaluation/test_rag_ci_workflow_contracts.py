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
    assert "multiquery:" in smoke
    # 生产检索链默认启用 MultiQuery：受信任评测默认按生产口径执行，
    # 关闭仅作为对照（dispatch 显式传 false）。
    assert "type: boolean\n        default: true" in smoke
    assert (
        "EVAL_MULTIQUERY: ${{ github.event_name == 'push' && 'true' || inputs.multiquery }}"
        in smoke
    )
    assert 'if [[ "$EVAL_MULTIQUERY" == "true" ]]; then' in smoke
    assert 'MULTIQUERY_ARGS+=(--multiquery)' in smoke
    assert 'if [[ "$EVENT_NAME" == "push" && "$BASE_SHA" == "0000000000000000000000000000000000000000" ]]; then' in smoke
    assert "BASE_SHA\" || -z \"$HEAD_SHA" in smoke
    assert "git diff --name-only \"$BASE_SHA\" \"$HEAD_SHA\" > \"$changed_paths\"" in smoke
    assert (
        "python3 .github/scripts/rag_smoke_path_filter.py --event \"$EVENT_NAME\" "
        "< \"$changed_paths\""
    ) in smoke
    assert "EVAL_TRIGGER: ${{ github.event_name }}" in smoke
    assert "EVAL_TRIGGER: ${{ github.event_name }}" in regression
    assert "python scripts/seed_eval_model_bindings.py --probe --target-db agent_memory_test" not in smoke
    assert "python scripts/seed_eval_model_bindings.py --probe --target-db agent_memory_test" in regression
    assert smoke.index("Snapshot usage ledger before paid provider calls") < smoke.index(
        "Import baseline evaluation fixtures"
    ) < smoke.index("Run RAG smoke evaluation")
    # 阶段请求预算按真实链路实测标定（2026-10-18 main run）：fixture 冷索引
    # ~80 calls / 45k tokens / CNY 0.10；eval 阶段含 rerank ~25k tokens，
    # MultiQuery 再增 ~8 次 chat。整次 run 仍由总 envelope 校验。
    assert 'LLM_REQUEST_MAX_CALLS: "128"' in smoke
    assert 'LLM_REQUEST_MAX_TOKENS: "96000"' in smoke
    assert 'LLM_REQUEST_MAX_COST: "0.40"' in smoke
    # MultiQuery 改写必须留够思考型模型的完成时间，同时小于检索 deadline。
    assert 'RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS:' not in smoke
    assert 'LLM_REQUEST_MAX_CALLS: "160"' in smoke
    assert 'LLM_REQUEST_MAX_TOKENS: "120000"' in smoke
    assert 'LLM_REQUEST_MAX_COST: "0.15"' in smoke
    # 整次 run envelope（含 fixture 冷索引）：2026-10-18 实测 108 calls /
    # 106,330 tokens / CNY 0.116，上限取约 1.8 倍实测值；费用上限不变。
    assert 'usage_calls <= 200' in smoke
    assert 'usage_tokens <= 200000' in smoke
    assert 'usage_cost <= Decimal("0.50")' in smoke
    assert 'budget_envelope_verification' in smoke
    assert 'tenant_id IS DISTINCT FROM %s' in smoke
    assert 'user_id IS DISTINCT FROM %s' in smoke
    for workflow in (smoke, regression):
        assert "PROMPT_EVAL_APPROVED_PRICES_JSON" not in workflow
        assert "--require-approved-prices" in workflow
        assert "PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL" in workflow
        assert "--verify-pricing" in workflow
    # 真实评测账本写入依赖此开关；两个 workflow 使用独立临时数据库。
    assert 'OBS_ANALYTICS_ENABLED: "true"' in smoke
    assert 'OBS_ANALYTICS_ENABLED: "true"' in regression
    expected_vector_dim = "${{ vars.PROMPT_EVAL_TEST_EMBEDDING_DIMENSIONS || '1024' }}"
    assert f"PROMPT_EVAL_TEST_EMBEDDING_DIMENSIONS: {expected_vector_dim}" in smoke
    assert f"VECTOR_PG_DIM: {expected_vector_dim}" in smoke
    assert f"PROMPT_EVAL_TEST_EMBEDDING_DIMENSIONS: {expected_vector_dim}" in regression
    assert f"VECTOR_PG_DIM: {expected_vector_dim}" in regression

    # Both workflows summarize the Billing V2 base-currency field (CNY), not
    # the deprecated compatibility amount whose currency can be mixed.
    for workflow in (smoke, regression):
        assert "sum(coalesce(billed_cost_cny, 0))" in workflow
        assert '"cost_verification": cost_verification' in workflow
        assert '"unverified_calls": unverified_calls' in workflow
        assert '"cost_status": cost_status or "unknown"' in workflow
        assert '"UNVERIFIED" if query_failed or not rows or unverified_calls' in workflow
        assert '"verification_error": "ledger_query_failed" if query_failed else None' in workflow
        assert "sum(coalesce(cost_usd, 0))" not in workflow

    assert "GROUP BY component, model, provider, cost_status, pricing_source" in smoke
    assert '"pricing_source": r[4] or "unknown"' in smoke
    assert 'else round(float(r[7] or 0), 6)' in smoke
    assert "GROUP BY model, provider, cost_status, pricing_source" in regression
    assert '"pricing_source": r[3] or "unknown"' in regression
    assert 'else round(float(r[6] or 0), 6)' in regression

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

        def execute(self, query, _params=None):
            self._query = query

        def fetchall(self):
            if "reservation_per_call" in self._query:
                return []
            if "GROUP BY component, model" in self._query:
                return [
                    (
                        "embedding", "embedding-model", "provider-a", "unpriced",
                        "unpriced", 1, 12, 0,
                    ),
                    (
                        "llm", "chat-model", "provider-b", "estimated",
                        "registry_fallback", 2, 40, 0.02,
                    ),
                ]
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

        def fetchone(self):
            if "count(*) FILTER" in self._query:
                return (3, 0, 52, 0.02)
            return None

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setenv("MEMORY_PGDATABASE", "isolated-test-db")
    monkeypatch.setenv("EVAL_TRIGGERED_BY", "github-actions-rag-smoke")
    monkeypatch.setenv("EVAL_RUN_ID", "rag-smoke-contract-test")
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


def test_cost_artifact_marks_ledger_query_failure_unverified(
    tmp_path, monkeypatch, capsys
):
    """账本查询异常也留下脱敏 UNVERIFIED artifact。"""

    def _raise_connection_error(**_kwargs):
        raise RuntimeError("credential-sentinel-must-not-leak")

    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=_raise_connection_error),
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
        assert artifact["verification_error"] == "ledger_query_failed"
        assert artifact["usage"] == []
        assert "credential-sentinel-must-not-leak" not in capsys.readouterr().out
