"""PR Smoke required gate and Billing V2 artifact contracts."""
import base64
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_rag_required_gate_and_cost_artifacts_follow_ci_contract():
    smoke = (ROOT / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    regression = (ROOT / ".github/workflows/rag_regression.yml").read_text(encoding="utf-8")

    # The stable required check distinguishes offline deferral from an actual
    # Live pass; a skipped paid job alone is never reported as Live success.
    assert "rag-smoke-gate:\n    name: RAG Smoke (8 cases)" in smoke
    assert "needs: [detect-changes, rag-smoke-pr-static, rag-smoke]" in smoke
    assert "if: ${{ always() }}" in smoke
    assert 'PR_PREFLIGHT_RESULT" != "success" || "$SMOKE_RESULT" != "skipped"' in smoke
    assert '"$SMOKE_RESULT" != "success"' in smoke
    assert "RAG Smoke Evaluation (8 cases, real API)" in smoke
    assert "github.event_name == 'workflow_dispatch' &&" in smoke
    assert "inputs.run_live_eval == true" in smoke
    assert "github.ref == 'refs/heads/main'" in smoke
    live_condition = smoke.split("  rag-smoke:\n", 1)[1].split("    name:", 1)[0]
    assert "github.event_name == 'push'" not in live_condition
    assert "github.event_name == 'pull_request' ||" in smoke
    assert "github.event_name == 'workflow_dispatch' && inputs.run_live_eval != true" in smoke
    assert "if [[ \"$EVENT_NAME\" == \"push\" ]]; then" in smoke
    assert "DEFERRED: offline PR checks passed" in smoke
    assert "Offline dispatch requires successful free preflight" in smoke
    assert "Explicit Live dispatch requires a successful real 8-case evaluation" in smoke
    assert "github.event.pull_request.base.sha || github.event.before" in smoke
    assert "github.event.pull_request.head.sha || github.sha" in smoke
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" ]]; then' in smoke
    assert "run_live_eval:" in smoke
    assert "default: false" in smoke
    assert "multiquery:" in smoke
    # 生产检索链默认启用 MultiQuery：受信任评测默认按生产口径执行，
    # 关闭仅作为对照（dispatch 显式传 false）。
    assert "type: boolean\n        default: true" in smoke
    assert "EVAL_MULTIQUERY: ${{ inputs.multiquery }}" in smoke
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


def test_production_release_workflows_auto_deploy_verified_main_digests():
    """自动部署只接受最新且验证通过的 main 镜像摘要。"""
    publish_path = ROOT / ".github/workflows/publish-images.yml"
    deploy_path = ROOT / ".github/workflows/deploy-production.yml"
    compose_path = ROOT / "部署/prod-4c8g/docker-compose.release.yml"
    release_path = ROOT / "scripts/release.sh"

    publish = yaml.load(publish_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    deploy = yaml.load(deploy_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    release_compose = yaml.load(
        compose_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )

    assert publish["on"]["push"]["branches"] == ["main"]
    assert ".github/scripts/**" in publish["on"]["push"]["paths"]
    assert "pull_request" not in publish["on"]
    assert publish["permissions"]["packages"] == "write"
    publish_text = publish_path.read_text(encoding="utf-8")
    assert "packages: write" in publish_text
    assert "production-image-digests" in publish_text
    assert "outputs.digest" in publish_text

    assert set(deploy["on"]) == {"workflow_run", "workflow_dispatch"}
    assert deploy["on"]["workflow_run"]["workflows"] == [
        "Publish production images"
    ]
    assert deploy["on"]["workflow_run"]["branches"] == ["main"]
    verify_job = deploy["jobs"]["verify-release"]
    deploy_job = deploy["jobs"]["deploy"]
    deploy_step_names = [step["name"] for step in deploy_job["steps"]]
    assert "workflow_run.conclusion == 'success'" in verify_job["if"]
    assert verify_job["runs-on"] == "ubuntu-latest"
    assert deploy_job["runs-on"] == [
        "self-hosted",
        "linux",
        "x64",
        "agent-production-deploy",
    ]
    assert deploy_job["environment"]["name"] == "production"
    prepare_credentials_index = deploy_step_names.index(
        "Prepare isolated Docker credentials"
    )
    login_index = deploy_step_names.index("Log in to GHCR")
    pull_index = deploy_step_names.index("Pull and deploy the verified digests")
    assert prepare_credentials_index < login_index < pull_index
    deploy_text = deploy_path.read_text(encoding="utf-8")
    assert "secrets.RELEASE_PASSWORD" not in deploy_text.split("  deploy:\n", 1)[0]
    assert "GHCR_TOKEN" not in deploy_text
    assert "docker logout ghcr.io" in deploy_text
    assert 'expected_path = ".github/workflows/publish-images.yml"' in deploy_text
    assert 'run.get("path") != expected_path' in deploy_text
    assert 'compare/${sha}...main' in deploy_text
    assert 'github.event_name == \'workflow_run\'' in deploy_text
    assert '"$latest_main_sha" != "$IMAGE_SHA"' in deploy_text
    assert '--repo "$GITHUB_REPOSITORY"' in deploy_text
    release_text = release_path.read_text(encoding="utf-8")
    assert 'if [ "$SKIP_BUILD" = "true" ]; then' in release_text
    assert "docker compose run --rm --no-deps" in release_text
    assert release_text.index("docker compose run --rm db-migrate") < (
        release_text.index("backend/scripts/verify_migration_state.py")
    )
    assert "PG_AUTHORITY_HOST=postgres" in release_text

    env_template = ROOT / "部署/prod-4c8g/env.production"
    env_template_text = env_template.read_text(encoding="utf-8")
    assert "LLM_BUDGET_MODE=observe" in env_template_text
    assert "validate_production_env.py" in deploy_text

    migration_gate_text = (ROOT / "backend/scripts/verify_release_gate.py").read_text(
        encoding="utf-8"
    )
    assert '"docker", "compose", "run", "--rm", "--no-deps"' in migration_gate_text
    assert '"backend/scripts/verify_migration_state.py"' in migration_gate_text
    assert "DETAIL_KEYS" in migration_gate_text

    services = release_compose["services"]
    backend_services = (
        "app",
        "rag-service",
        "mcp-service",
        "business-mock",
        "agent-worker",
        "rag-index-worker",
        "maintenance-worker",
        "report-worker",
        "beat",
        "cs-dispatcher",
        "db-migrate",
    )
    assert set(services) == set(backend_services) | {
        "frontend",
        "frontend-admin",
        "frontend-cs",
    }
    assert all(
        services[name]["image"] == "${IMAGE_BACKEND:?IMAGE_BACKEND is required}"
        for name in backend_services
    )
    frontend_images = {
        "frontend": "${IMAGE_FRONTEND:?IMAGE_FRONTEND is required}",
        "frontend-admin": "${IMAGE_ADMIN:?IMAGE_ADMIN is required}",
        "frontend-cs": "${IMAGE_CS:?IMAGE_CS is required}",
    }
    assert all(
        services[name]["image"] == image
        for name, image in frontend_images.items()
    )
    assert "docker compose up -d --no-build" in release_text


def test_production_env_preflight_checks_mode_and_encryption_key(tmp_path):
    validator = ROOT / ".github/scripts/validate_production_env.py"
    valid_key = base64.urlsafe_b64encode(b"x" * 32).decode("ascii")
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        f"LLM_BUDGET_MODE=observe\nSECRETS_ENCRYPTION_KEY={valid_key}\n",
        encoding="utf-8",
    )

    valid = subprocess.run(
        [sys.executable, str(validator), str(env_file)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert valid.returncode == 0
    assert "required settings are valid" in valid.stdout

    env_file.write_text(
        "LLM_BUDGET_MODE=invalid\nSECRETS_ENCRYPTION_KEY=" + valid_key + "\n",
        encoding="utf-8",
    )
    invalid = subprocess.run(
        [sys.executable, str(validator), str(env_file)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert invalid.returncode == 1
    assert "LLM_BUDGET_MODE" in invalid.stderr

    env_file.write_text(
        "LLM_BUDGET_MODE=observe\nSECRETS_ENCRYPTION_KEY=change_me\n",
        encoding="utf-8",
    )
    invalid = subprocess.run(
        [sys.executable, str(validator), str(env_file)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert invalid.returncode == 1
    assert "SECRETS_ENCRYPTION_KEY" in invalid.stderr
    assert "change_me" not in invalid.stdout + invalid.stderr


def test_release_gate_migration_uses_compose_production_environment(monkeypatch):
    from backend.scripts import verify_release_gate

    captured = {}

    def _run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout="result: MIGRATION_STATE_OK\n",
            stderr="",
        )

    monkeypatch.setattr(verify_release_gate.subprocess, "run", _run)
    monkeypatch.setattr(verify_release_gate, "DETAIL", {})

    assert verify_release_gate.g1_migration() is True
    assert captured["command"][:4] == ["docker", "compose", "run", "--rm"]
    assert "PG_AUTHORITY_HOST=postgres" in captured["command"]
    assert "db-migrate" in captured["command"]
    assert "backend/scripts/verify_migration_state.py" in captured["command"]
    assert captured["kwargs"]["cwd"] == verify_release_gate.RELEASE_ROOT
