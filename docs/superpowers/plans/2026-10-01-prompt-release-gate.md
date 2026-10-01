# Prompt Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a Prompt candidate-release gate in which the selected evaluation dataset and full runtime provenance are fixed before external-model CI, production publish is blocked until the result passes, and the admin UI verifies hot reload plus Prompt/Tool versions in traces.

**Architecture:** Keep Prompt versions and environment releases separate. A release record pins `prompt_key`, version, evaluation suite, dataset/KB scope, model binding and Tool contract fingerprints; a local or GitHub dispatcher runs the evaluation and updates the record idempotently. Only an approved passing record may switch the production alias/active version and emit the existing hot-reload epoch notification.

**Tech Stack:** FastAPI, PostgreSQL migrations and repositories, existing `backend.evaluation` service, GitHub Actions, Next.js 14/React/TypeScript/Vitest, existing Prompt runtime hot reload, existing Trace collector and Tool contract lock.

**Spec:** `docs/superpowers/specs/2026-10-01-prompt-release-gate-design.md`

## Global Constraints

- Preserve the existing Prompt DB as the runtime source of truth; do not copy Tool code or production secrets into the admin UI or repository.
- Keep `data/eval_runs/` as full report authority and `ai.eval_run_records` as the run index; add provenance fields without replacing the existing report files.
- Production `active_version` and `prompt_aliases.production` must not change when evaluation fails, callback verification fails, or runtime hot reload is unhealthy.
- Prompt and Tool version snapshots must be request-scoped and immutable for in-flight traces.
- New Tool contract information must derive from `backend/tool_contracts.lock.json`; do not hand-maintain a second Tool contract table.
- Backend focused pytest commands must include `--no-cov`; Python changes require `py_compile` and focused pytest before broader verification.
- Preserve unrelated dirty worktree changes; inspect `git diff -- <path>` before modifying any already-dirty file.
- GitHub CI uses a test DB and GitHub Environment Secrets; it must never connect to the production DB.

## Review Focus

- Failed or unverified evaluation must leave `active_version`, production alias, runtime epoch and live Prompt unchanged; test the API and repository transaction boundary in Task 2.
- Duplicate or out-of-order GitHub callbacks must be idempotent and must not turn a terminal failed/published release back into running; test callback state transitions in Task 3.
- A release must evaluate the candidate version and exact dataset/KB scope rather than silently using the current production Prompt or a different fixture set; test provenance in Task 4.
- Partial hot reload must be visible as `stale`/`degraded` and must not be reported as fully healthy; test runtime status aggregation in Task 5.
- Traces must contain both Prompt versions and Tool contract identity while historical traces remain unchanged after publish/rollback; test real trace metadata in Task 6.

---

### Task 1: Add Prompt release persistence and domain model

**Files:**
- Create: `backend/sql/migrations/069_prompt_release_records.sql`
- Create: `backend/prompts/release_models.py`
- Create: `backend/prompts/release_repository.py`
- Create: `backend/prompts/release_service.py`
- Test: `backend/tests/prompts/test_prompt_release_service.py`

**Interfaces:**
- Produces `PromptReleaseStatus`, `PromptReleaseRecord`, `PromptReleaseRepository`, and `PromptReleaseService` for the API and dispatch tasks.
- `PromptReleaseService.create_release(key, version, suite, dataset_version, actor, executor) -> PromptReleaseRecord` creates a pending release without changing production.
- `PromptReleaseService.mark_running(release_id, external_run_id) -> PromptReleaseRecord` is idempotent for the same run id.
- `PromptReleaseService.record_result(release_id, result, actor) -> PromptReleaseRecord` accepts only a verified terminal result and stores metrics/provenance.
- `PromptReleaseService.approve(release_id, actor) -> PromptReleaseRecord` enforces the existing risk-level permission decision supplied by the route.
- `PromptReleaseService.publish(release_id, actor) -> PromptReleaseRecord` performs the production alias/active-version change only after the gate and emits the existing hot-reload notification.

- [ ] **Step 1: Write the failing state-machine tests**

```python
def test_failed_release_does_not_change_active_version(repo, prompt_service):
    release = prompt_service.create_release(
        key="test.prompt", version=2, suite="pr_baseline",
        dataset_version={"version": "5.0.0"}, actor="admin",
        executor="local",
    )
    prompt_service.record_result(release.id, {"status": "failed"}, "eval")

    with pytest.raises(PublishGateError):
        prompt_service.publish(release.id, "admin")

    assert repo.get_active_version("test.prompt") == 1


def test_passing_release_can_be_approved_and_published(repo, prompt_service):
    release = prompt_service.create_release(
        key="test.prompt", version=2, suite="pr_baseline",
        dataset_version={"version": "5.0.0"}, actor="admin",
        executor="local",
    )
    prompt_service.record_result(
        release.id,
        {"status": "passed", "run_id": "eval-1", "metrics": {"mrr": 0.9}},
        "eval",
    )
    prompt_service.approve(release.id, "admin")
    published = prompt_service.publish(release.id, "admin")

    assert published.status == PromptReleaseStatus.PUBLISHED
    assert repo.get_active_version("test.prompt") == 2
```

- [ ] **Step 2: Run the focused tests and verify the intended RED failure**

Run: `D:/Python/python.exe -m pytest backend/tests/prompts/test_prompt_release_service.py -q --no-cov`

Expected: FAIL because the release model, persistence table and gate service do not exist yet.

- [ ] **Step 3: Add the migration and minimal repository/service implementation**

Create `ai.prompt_release_records` with `release_id`, `prompt_key`, `version`, `target_env`, `status`, `eval_suite`, `dataset_provenance`, `prompt_snapshot`, `tool_contract_fingerprint`, `model_binding_fingerprint`, `eval_run_id`, `external_run_id`, `metrics`, `failure_reason`, `created_by`, `approved_by`, `published_at`, timestamps and a unique idempotency key on `(prompt_key, version, target_env, active_release_key)`. Use `CHECK` constraints for `pending/running/failed/passed/approved/published/rolled_back` and a unique external run id.

Implement repository methods with parameterized SQL and a compare-and-set update for terminal states. `publish()` must call the existing PromptService production publication path only after the record is `approved`, then mark the release published in the same guarded operation; a failed hot-reload notification must be visible in the release result and must not silently claim all processes are healthy.

- [ ] **Step 4: Run the focused tests and syntax validation**

Run: `D:/Python/python.exe -m pytest backend/tests/prompts/test_prompt_release_service.py -q --no-cov`

Expected: PASS, including failed-release protection, passing publish, duplicate terminal update rejection, and rollback state preservation.

Run: `D:/Python/python.exe -m py_compile backend/prompts/release_models.py backend/prompts/release_repository.py backend/prompts/release_service.py`

Expected: exit code 0.

- [ ] **Step 5: Commit only Task 1 paths**

```powershell
git add -A -- backend/sql/migrations/069_prompt_release_records.sql backend/prompts/release_models.py backend/prompts/release_repository.py backend/prompts/release_service.py backend/tests/prompts/test_prompt_release_service.py
git commit -m "feat: add prompt release gate persistence" -- backend/sql/migrations/069_prompt_release_records.sql backend/prompts/release_models.py backend/prompts/release_repository.py backend/prompts/release_service.py backend/tests/prompts/test_prompt_release_service.py
```

### Task 2: Add release APIs, permissions and backward-compatible publish gating

**Files:**
- Create: `backend/app/api/routes/prompt_releases.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/api/routes/prompts.py`
- Test: `backend/tests/api/test_prompt_release_api.py`

**Interfaces:**
- `POST /api/prompts/{key}/versions/{version}/release` accepts `{suite, dataset_version, executor}` and returns the release record.
- `GET /api/prompts/{key}/releases` returns newest-first records for the Prompt.
- `GET /api/prompts/{key}/releases/{release_id}` returns release state, metrics, provenance and runtime status.
- `POST /api/prompts/{key}/releases/{release_id}/approve` enforces risk-level permissions.
- `POST /api/prompts/{key}/releases/{release_id}/publish` performs the guarded production publish.
- Existing `POST /api/prompts/{key}/publish` calls the same gate and returns HTTP 409 with a structured error when no passing/approved release exists.

- [ ] **Step 1: Write failing API tests**

```python
def test_publish_returns_409_before_release_passes(client, prompt_row):
    response = client.post(
        "/api/prompts/test.prompt/publish", json={"version": 2},
        headers=admin_headers(),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PROMPT_RELEASE_GATE_BLOCKED"


def test_release_list_exposes_dataset_and_model_provenance(client, release):
    response = client.get(
        f"/api/prompts/test.prompt/releases",
        headers=admin_headers(),
    )
    body = response.json()
    assert body["items"][0]["dataset_provenance"]["suite"] == "pr_baseline"
    assert "model_binding_fingerprint" in body["items"][0]
```

- [ ] **Step 2: Run the API tests and verify RED**

Run: `D:/Python/python.exe -m pytest backend/tests/api/test_prompt_release_api.py -q --no-cov`

Expected: FAIL because the routes and gate response do not exist.

- [ ] **Step 3: Implement the routes and error contract**

Use `resolve_operator_role` as the only identity source. Use `editor/admin/super_admin` permissions for evaluation submission, require `admin/super_admin` for high-risk approval, and do not accept operator identity from request headers. Keep release creation idempotent for the same Prompt/version/target and return `202` while an asynchronous evaluator is running.

- [ ] **Step 4: Run focused API and existing Prompt tests**

Run: `D:/Python/python.exe -m pytest backend/tests/api/test_prompt_release_api.py backend/tests/api/test_prompts_api.py backend/tests/prompts/test_prompt_alias.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit only Task 2 paths**

```powershell
git add -A -- backend/app/api/routes/prompt_releases.py backend/app/api/router.py backend/app/api/routes/prompts.py backend/tests/api/test_prompt_release_api.py
git commit -m "feat: gate prompt publishing on evaluated releases" -- backend/app/api/routes/prompt_releases.py backend/app/api/router.py backend/app/api/routes/prompts.py backend/tests/api/test_prompt_release_api.py
```

### Task 3: Add local/external evaluation dispatch and GitHub callback

**Files:**
- Create: `backend/prompts/eval_dispatch.py`
- Create: `backend/app/api/routes/prompt_eval_callback.py`
- Create: `backend/scripts/seed_prompt_eval_db.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/config/redis.py` or existing task configuration only if the current agent queue requires registration
- Test: `backend/tests/prompts/test_prompt_eval_dispatch.py`
- Test: `backend/tests/api/test_prompt_eval_callback.py`

**Interfaces:**
- `PromptEvalDispatcher.dispatch(release: PromptReleaseRecord) -> DispatchResult` selects `local` or `github` without exposing credentials to the frontend.
- `LocalPromptEvalDispatcher` calls the existing `backend.evaluation` service with the selected suite and records the run id.
- `GitHubPromptEvalDispatcher` sends `workflow_dispatch` or `repository_dispatch` with `release_id`, `prompt_key`, `version` and suite; missing GitHub configuration returns a structured `DISPATCH_NOT_CONFIGURED` failure.
- Callback verifies `HMAC-SHA256` over the raw request body using a backend secret, rejects stale timestamps and wrong release/run ids, and calls `record_result()` exactly once for a terminal run.

- [ ] **Step 1: Write failing dispatch and callback tests**

```python
def test_callback_rejects_invalid_signature(client, release):
    response = client.post(
        "/internal/prompt-evals/callback",
        content=json.dumps({"release_id": release.id, "status": "passed"}),
        headers={"X-Prompt-Eval-Timestamp": "now", "X-Prompt-Eval-Signature": "bad"},
    )
    assert response.status_code == 401


def test_duplicate_pass_callback_is_idempotent(client, passed_release, signed_callback):
    first = client.post("/internal/prompt-evals/callback", **signed_callback(passed_release))
    second = client.post("/internal/prompt-evals/callback", **signed_callback(passed_release))
    assert first.status_code == 200
    assert second.status_code == 200
    assert load_release(passed_release.id).status == "passed"
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `D:/Python/python.exe -m pytest backend/tests/prompts/test_prompt_eval_dispatch.py backend/tests/api/test_prompt_eval_callback.py -q --no-cov`

Expected: FAIL because dispatchers, callback route and signature validation do not exist.

- [ ] **Step 3: Implement local and GitHub dispatchers**

The local executor must use the current DB-bound external model and existing suite selection; it must record `trigger=prompt_publish` and `triggered_by` without claiming that the model is local. The GitHub executor must never use the production DB and must pass only release identifiers and suite metadata; secrets remain in the GitHub Environment.

- [ ] **Step 4: Add callback route and seed helper**

The seed helper creates a test-only provider/model binding from environment variables, runs only against the configured test database, and refuses a production-looking hostname. The callback route returns the current release state and never calls production publish automatically unless the release policy explicitly allows auto-approval.

- [ ] **Step 5: Run focused tests and compile**

Run: `D:/Python/python.exe -m pytest backend/tests/prompts/test_prompt_eval_dispatch.py backend/tests/api/test_prompt_eval_callback.py -q --no-cov`

Expected: PASS.

Run: `D:/Python/python.exe -m py_compile backend/prompts/eval_dispatch.py backend/app/api/routes/prompt_eval_callback.py backend/scripts/seed_prompt_eval_db.py`

Expected: exit code 0.

- [ ] **Step 6: Commit only Task 3 paths**

```powershell
git add -A -- backend/prompts/eval_dispatch.py backend/app/api/routes/prompt_eval_callback.py backend/scripts/seed_prompt_eval_db.py backend/app/api/router.py backend/tests/prompts/test_prompt_eval_dispatch.py backend/tests/api/test_prompt_eval_callback.py
git commit -m "feat: dispatch prompt evaluations and accept signed results" -- backend/prompts/eval_dispatch.py backend/app/api/routes/prompt_eval_callback.py backend/scripts/seed_prompt_eval_db.py backend/app/api/router.py backend/tests/prompts/test_prompt_eval_dispatch.py backend/tests/api/test_prompt_eval_callback.py
```

### Task 4: Persist evaluation-set and runtime provenance

**Files:**
- Create: `backend/evaluation/provenance.py`
- Modify: `backend/evaluation/config.py`
- Modify: `backend/evaluation/service.py`
- Modify: `backend/evaluation/storage.py`
- Modify: `backend/evaluation/run_records.py`
- Modify: `backend/evaluation/runners/rag.py`
- Test: `backend/tests/evaluation/test_eval_provenance.py`

**Interfaces:**
- `build_eval_provenance(config, report) -> dict` returns `suite`, `dataset_version`, `kb_id`, `fixture_set`, `version_id`, `prompt_snapshot`, `model_binding_fingerprint`, `tool_contract_fingerprint` and `git_sha`.
- `EvalConfig` accepts an optional candidate Prompt snapshot and `release_id`; the snapshot is used for the run and persisted in report metadata.
- Existing `ai.eval_run_records.dataset_version` stores the structured suite/snapshot object without removing the existing summary fields.

- [ ] **Step 1: Write failing provenance tests**

```python
def test_rag_provenance_contains_suite_and_scope():
    report = run_report_for_selection("pr_baseline")
    provenance = build_eval_provenance(report.config, report)
    assert provenance["suite"] == "pr_baseline"
    assert provenance["kb_id"] == "rag_eval_kb"
    assert provenance["fixture_set"] == "baseline"
    assert provenance["dataset_version"]


def test_prompt_candidate_snapshot_is_not_replaced_by_current_active_version():
    config = EvalConfig(
        module="rag", selection="pr_baseline",
        prompt_versions={"test.prompt": "2"}, release_id="rel-1",
    )
    assert build_eval_provenance(config, fake_report(config))["prompt_snapshot"]["test.prompt"] == "2"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_eval_provenance.py -q --no-cov`

Expected: FAIL because structured suite scope and candidate snapshot are not yet propagated to the report/run record.

- [ ] **Step 3: Implement provenance collection**

Use the existing RAG `evaluation_scope` as the source for `kb_id`, `fixture_set`, `multiquery` and `version_id`; use the manifest/suite file for dataset version; use existing prompt snapshot and Tool lock readers for fingerprints. Do not invent a second dataset version string or hand-maintain a Tool mapping.

- [ ] **Step 4: Run existing RAG evaluation metadata tests and the new tests**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_eval_provenance.py backend/tests/evaluation/test_rag_eval_runner_scope.py backend/tests/evaluation/test_rag_eval_snapshot.py -q --no-cov`

Expected: PASS, with no change to existing case-level metrics.

- [ ] **Step 5: Commit only Task 4 paths**

```powershell
git add -A -- backend/evaluation/provenance.py backend/evaluation/config.py backend/evaluation/service.py backend/evaluation/storage.py backend/evaluation/run_records.py backend/evaluation/runners/rag.py backend/tests/evaluation/test_eval_provenance.py
git commit -m "feat: persist evaluation dataset provenance" -- backend/evaluation/provenance.py backend/evaluation/config.py backend/evaluation/service.py backend/evaluation/storage.py backend/evaluation/run_records.py backend/evaluation/runners/rag.py backend/tests/evaluation/test_eval_provenance.py
```

### Task 5: Add GitHub Prompt evaluation workflow

**Files:**
- Create: `.github/workflows/prompt_eval.yml`
- Modify: `.env.example`
- Test: `backend/tests/test_prompt_eval_workflow.py`

**Interfaces:**
- Workflow events: `workflow_dispatch` and `repository_dispatch` only; payload fields are `release_id`, `prompt_key`, `version`, `suite` and `callback_url`.
- Required secrets are GitHub Environment-scoped: external model credentials, registry encryption key and callback secret.
- Workflow publishes `report.json`, Markdown output and a machine-readable callback result as artifacts.

- [ ] **Step 1: Write the workflow contract test**

```python
def test_prompt_eval_workflow_is_manual_or_repository_dispatch_only():
    workflow = yaml.safe_load(Path(".github/workflows/prompt_eval.yml").read_text())
    assert "workflow_dispatch" in workflow["on"]
    assert "repository_dispatch" in workflow["on"]
    assert "pull_request" not in workflow["on"]
    assert "production" not in json.dumps(workflow["jobs"])
```

- [ ] **Step 2: Run the test and verify RED**

Run: `D:/Python/python.exe -m pytest backend/tests/test_prompt_eval_workflow.py -q --no-cov`

Expected: FAIL because the active workflow does not exist.

- [ ] **Step 3: Add the workflow**

The job starts only a test PostgreSQL service, checks out the requested commit, installs locked dependencies, runs the test registry seed, executes the selected external-model evaluation, uploads `data/eval_runs/`, and sends a signed callback in an `if: always()` step. A callback failure must fail the job or leave the release unresolved; it must never mark the release passed locally.

- [ ] **Step 4: Run workflow contract and YAML validation**

Run: `D:/Python/python.exe -m pytest backend/tests/test_prompt_eval_workflow.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit only Task 5 paths**

```powershell
git add -A -- .github/workflows/prompt_eval.yml .env.example backend/tests/test_prompt_eval_workflow.py
git commit -m "ci: add prompt release evaluation workflow" -- .github/workflows/prompt_eval.yml .env.example backend/tests/test_prompt_eval_workflow.py
```

### Task 6: Build the Prompt release console and Tool governance summary

**Files:**
- Create: `frontend-admin/src/components/prompts/PromptReleasePanel.tsx`
- Create: `frontend-admin/src/components/prompts/PromptReleasePanel.test.tsx`
- Modify: `frontend-admin/src/api/prompts.ts`
- Modify: `frontend-admin/src/app/prompts/[key]/page.tsx`
- Modify: `frontend-admin/src/components/prompts/StatusPipeline.tsx` only where release status needs separate display

**Interfaces:**
- API client methods: `createRelease`, `listReleases`, `getRelease`, `approveRelease`, `publishRelease`.
- Panel displays candidate version, suite/dataset provenance, executor, model binding, metrics, failure reason, approver, release status and runtime processes.
- Publish button is disabled unless the selected release is `approved`; a stale/degraded runtime displays a warning and keeps rollback visible.

- [ ] **Step 1: Write failing component tests**

```tsx
it('does not enable production publish while evaluation is running', () => {
  render(<PromptReleasePanel release={runningRelease} onPublish={vi.fn()} />)
  expect(screen.getByRole('button', { name: '发布到生产' })).toBeDisabled()
})

it('shows dataset and model provenance for a passed release', () => {
  render(<PromptReleasePanel release={passedRelease} onPublish={vi.fn()} />)
  expect(screen.getByText('pr_baseline')).toBeInTheDocument()
  expect(screen.getByText(/模型绑定/)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the frontend test and verify RED**

Run: `npm test -- --run src/components/prompts/PromptReleasePanel.test.tsx` from `frontend-admin`.

Expected: FAIL because the panel and release types do not exist.

- [ ] **Step 3: Implement the panel and API client**

Use the existing admin visual language and Prompt Runtime strip. Keep the release panel left-aligned and information-dense: one status rail, one provenance row, one metrics row and explicit primary actions. Do not add a second navigation system or a Tool code editor. The Tool section is read-only and links to the existing Tool governance statistics.

- [ ] **Step 4: Run frontend tests and typecheck**

Run: `npm test -- --run src/components/prompts/PromptReleasePanel.test.tsx` from `frontend-admin`.

Expected: PASS.

Run: `npx tsc --noEmit` from `frontend-admin`.

Expected: exit code 0.

- [ ] **Step 5: Commit only Task 6 paths**

```powershell
git add -A -- frontend-admin/src/components/prompts/PromptReleasePanel.tsx frontend-admin/src/components/prompts/PromptReleasePanel.test.tsx frontend-admin/src/api/prompts.ts frontend-admin/src/app/prompts/[key]/page.tsx frontend-admin/src/components/prompts/StatusPipeline.tsx
git commit -m "feat: add prompt release console" -- frontend-admin/src/components/prompts/PromptReleasePanel.tsx frontend-admin/src/components/prompts/PromptReleasePanel.test.tsx frontend-admin/src/api/prompts.ts frontend-admin/src/app/prompts/[key]/page.tsx frontend-admin/src/components/prompts/StatusPipeline.tsx
```

### Task 7: Verify Trace Prompt/Tool versions and hot reload

**Files:**
- Create: `backend/tests/observability/test_trace_version_provenance.py`
- Modify: `backend/skills/base.py` only if the test shows Tool identity is missing from the final span
- Modify: `backend/observability/tracer.py` only if the test shows Prompt usage is missing from the final record
- Modify: `backend/orchestration/graph/runner.py` only if request-level Prompt pin is not preserved

- [ ] **Step 1: Write failing integration-level trace assertions**

```python
def test_trace_contains_prompt_snapshot_and_tool_contract_hash(trace_collector, governed_tool):
    trace = run_one_tool_request(trace_collector, governed_tool)
    assert trace.tags["prompt_versions"]
    tool_spans = [span for span in trace.spans if span.type == "tool_call"]
    assert tool_spans
    assert tool_spans[0].metrics["contract_hash"]
    assert tool_spans[0].name
```

- [ ] **Step 2: Run the test and verify RED or prove the existing implementation already satisfies it**

Run: `D:/Python/python.exe -m pytest backend/tests/observability/test_trace_version_provenance.py -q --no-cov`

Expected: either a targeted failure identifying the missing field or PASS with no production change if the current dirty hot-reload/trace work already provides the required metadata.

- [ ] **Step 3: Add only the missing metadata path**

Keep Prompt versions in the request-start snapshot and Tool contract hash derived from the lock. Do not write full Prompt templates, API keys or Tool parameters into trace tags.

- [ ] **Step 4: Run Prompt pin, Tool contract and trace tests**

Run: `D:/Python/python.exe -m pytest backend/tests/prompts/test_prompt_pin.py backend/tests/prompts/test_hot_reload.py backend/tests/observability/test_trace_version_provenance.py backend/tests/test_tool_contract_lock.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit only Task 7 paths**

```powershell
git add -A -- backend/tests/observability/test_trace_version_provenance.py backend/skills/base.py backend/observability/tracer.py backend/orchestration/graph/runner.py
git commit -m "test: verify prompt and tool trace provenance" -- backend/tests/observability/test_trace_version_provenance.py backend/skills/base.py backend/observability/tracer.py backend/orchestration/graph/runner.py
```

### Task 8: Browser acceptance and full verification

**Files:**
- Create: `/tmp/playwright-test-prompt-release.js` (temporary only; do not add to the repository)
- Evidence: `/tmp/prompt-release-browser.png` and `/tmp/prompt-release-trace.png`

- [ ] **Step 1: Verify the baseline services without changing containers**

Run: `docker ps -a`, then `netstat -ano | findstr :3200` and `netstat -ano | findstr :8000`.

Expected: identify the existing admin/backend services and avoid restarting a service owned by another session.

- [ ] **Step 2: Detect the browser target before writing the Playwright script**

Run from the Playwright skill directory: `node -e "require('./lib/helpers').detectDevServers().then(s => console.log(JSON.stringify(s)))"`.

Expected: use the detected `http://127.0.0.1:3200` admin server if available; otherwise start only the missing service through `devctl.bat start admin /y` after the port check.

- [ ] **Step 3: Run the visible browser flow**

The script logs in with the local super-admin, opens a low-risk Prompt, creates a candidate version, submits a local/external-model evaluation, waits for the release status, asserts that publish is disabled before pass, publishes only after pass, then asserts the runtime status shows a higher epoch and the new Prompt version. It captures the release panel and runtime strip. It then sends a trace-producing request and asserts that the returned trace contains Prompt versions and Tool contract hash.

Run from the Playwright skill directory: `node run.js /tmp/playwright-test-prompt-release.js`.

Expected: visible browser completes without console errors; release status is `published`; runtime status is `healthy` or explicitly `stale` with the expected pending-sync message; trace contains both version classes.

- [ ] **Step 4: Run backend and frontend verification**

Run: `D:/Python/python.exe -m pytest backend/tests/prompts backend/tests/api/test_prompt_release_api.py backend/tests/api/test_prompts_api.py backend/tests/evaluation/test_eval_provenance.py backend/tests/observability/test_trace_version_provenance.py -q --no-cov`

Expected: all focused backend tests pass.

Run from `frontend-admin`: `npm test -- --run` and `npx tsc --noEmit`.

Expected: all frontend tests pass and TypeScript exits 0.

Run: `D:/Python/python.exe -m compileall -q backend/prompts backend/evaluation backend/app/api/routes`

Expected: exit code 0.

- [ ] **Step 5: Review the final diff and report any pre-existing failures**

Run: `git status --short` and `git diff --stat`.

Expected: only the task paths are attributed to this work; unrelated existing changes remain untouched and are listed separately in the final report.

