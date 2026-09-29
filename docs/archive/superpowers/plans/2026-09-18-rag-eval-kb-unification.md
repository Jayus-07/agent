# RAG 评测知识库统一实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `rag_test_kb` 与 `rag_100_docs` 统一到隔离的 `rag_eval_kb` 测试知识库，通过 `fixture_set` 区分日常回归、100 份专项评测和未来 20k 容量测试，同时保留旧入口兼容。

**Architecture:** 测试知识库统一使用 `kb_id=rag_eval_kb`、`audience=test`，文档元数据增加 `fixture_set` 和稳定 fixture ID。评测集使用 canonical `cases.jsonl` + suites，suite 显式声明语料范围；旧 KB ID 和旧目录在一个发布周期内只读兼容。生产 KB、授权集合和测试 KB 不互通。

**Tech Stack:** Python 3、Pydantic/现有评测模型、FastAPI 配置、PGVector、BM25、pytest、JSON/JSONL manifest。

**Spec:** `docs/superpowers/specs/2026-09-18-rag-eval-kb-unification-design.md`

## Global Constraints

- 不迁移生产文档，不修改生产 KB 的检索范围，不删除现有测试索引。
- `audience=test` 必须继续阻止员工/对客授权集合返回测试库。
- 评测集必须显式选择 suite/corpus，禁止依赖默认路径猜测。
- baseline 与 expanded_100 的指标不得混算；20k 只在独立 staging 容量环境运行。
- 迁移脚本必须幂等，失败文档不得被标记为 `active`。
- 所有新增代码、脚本和迁移步骤使用中文注释，说明兼容层原因和删除条件。
- 测试修改遵循项目 test-quality-guard 约束：强断言、只 mock 外部边界、实际运行并做假阳性自审。
- 局部 pytest 命令统一带 `--no-cov`；完成后运行项目规定的 registry/layer/ADR 回归。
- 不使用 `git reset --hard`、`git checkout --` 或宽范围清理命令；每个提交只包含当前任务文件。

## File Map

### Create

- `backend/evaluation/fixtures/rag_eval_kb/manifest.json`：统一 fixture catalog，引用 baseline 与 expanded_100 的文档和元数据。
- `backend/evaluation/dataset/fixture_catalog.py`：读取、校验和按 `fixture_set` 解析 catalog 的纯函数模块。
- `backend/evaluation/datasets/rag/suites/pr_baseline.json`：PR baseline suite。
- `backend/evaluation/datasets/rag/suites/expanded_100.json`：100 份专项评测 suite。
- `backend/evaluation/datasets/rag/suites/scale_20k.json`：复用专项问题集、声明 20k staging 语料范围的容量 suite。
- `backend/tests/evaluation/test_fixture_catalog.py`：manifest/catalog/schema 回归测试。
- `backend/tests/evaluation/test_rag_eval_suites.py`：suite、KB、fixture_set 对账测试。

### Modify

- `backend/config/knowledge_base.py`：增加 `rag_eval_kb`，声明测试受众和旧 KB 兼容别名。
- `backend/evaluation/dataset/loader.py`：支持 suite 的 `kb_id`、`fixture_set`、数据版本元信息，并把它们注入 `TestCase.metadata`。
- `backend/evaluation/service.py`：将 suite 运行上下文传递给 RAG runner，报告记录实际 KB、语料集和版本。
- `backend/evaluation/runners/rag.py`：构造测试 KB 的 `fixture_set` 过滤器，拒绝 suite 与 case/KB 不一致的静默降级；记录评测口径。
- `backend/evaluation/fixtures/rag_100_docs/manifest.json`：补充 `fixture_set=expanded_100` 和统一 KB 标识的兼容字段。
- `backend/evaluation/datasets/rag/cases.jsonl`：将现有 RC 用例切换到 `rag_eval_kb`，补 `source_fixture_set=baseline`。
- `backend/evaluation/datasets/rag_100_docs.json`：保留旧格式读取能力，生成或同步 RD 用例到 canonical JSONL，补 `source_fixture_set=expanded_100`。
- `backend/evaluation/datasets/rag_test_kb.json`：保留旧文件但增加迁移说明，禁止新增用例继续写入。
- `backend/scripts/ingest_eval_fixtures.py`：统一读取 catalog，写入 `rag_eval_kb` 和 `fixture_set`；保留旧命令参数和兼容提示。
- `backend/scripts/generate_test_docs.py`：baseline 生成结果写入统一 fixture catalog 的 baseline 区域，不再把 `data/docs/rag_test_kb` 作为唯一事实源。
- `backend/tests/conftest.py`：为评测 fixture 临时目录和测试 KB 提供隔离 fixture。
- `backend/tests/evaluation/test_eval_golden.py`：移除失效的 `--full` pytest 入口，改由 evaluation CLI/suite 统一驱动，并补 suite 口径断言。
- `backend/tests/rag/test_indexer_*` 或新增针对索引元数据的测试：验证 `fixture_set` 随文档、chunk、registry 全链路保留。
- `docs/rag_eval/README.md`、`docs/rag_eval/EVALUATION_MATRIX.md`、`docs/RAG_DESIGN.md`：更新实际命令、100/169 条数据口径、旧路径兼容和日常运行方式。

## Implementation Tasks

### Task 1: 建立统一 KB 和 fixture catalog 契约

**Files:**
- Create: `backend/evaluation/dataset/fixture_catalog.py`
- Create: `backend/evaluation/fixtures/rag_eval_kb/manifest.json`
- Modify: `backend/config/knowledge_base.py`
- Test: `backend/tests/evaluation/test_fixture_catalog.py`

**Interfaces:**
- `load_fixture_catalog(path: Path | None = None) -> FixtureCatalog`
- `FixtureCatalog.documents(fixture_set: str | None = None) -> list[FixtureDocument]`
- `FixtureCatalog.validate() -> None`
- `catalog_from_dict(data: dict) -> FixtureCatalog`
- `resolve_eval_profile(source_kb_id: str) -> EvalProfile`
- `FixtureDocument`: `fixture_doc_id`, `source_file`, `fixture_set`, `format`, `kb_id`, `metadata`
- `RAG_EVAL_KB_ID = "rag_eval_kb"`

- [ ] **Step 1: Write failing tests for catalog validation.**

```python
def test_catalog_has_one_kb_and_known_fixture_sets():
    catalog = load_fixture_catalog()
    catalog.validate()
    assert catalog.kb_id == "rag_eval_kb"
    assert {d.fixture_set for d in catalog.documents()} >= {
        "baseline", "expanded_100",
    }


def test_catalog_rejects_duplicate_fixture_doc_id():
    catalog = catalog_from_dict({"kb_id": "rag_eval_kb", "documents": [
        {"fixture_doc_id": "same", "fixture_set": "baseline", "source_file": "a.md"},
        {"fixture_doc_id": "same", "fixture_set": "expanded_100", "source_file": "b.md"},
    ]})
    with pytest.raises(ValueError, match="fixture_doc_id"):
        catalog.validate()
```

- [ ] **Step 2: Run the focused test and confirm it fails because the catalog API is absent.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_fixture_catalog.py -q --no-cov`

Expected: FAIL with an import or missing-catalog error.

- [ ] **Step 3: Implement the catalog dataclasses and validation.**

Implement only pure parsing/validation in `fixture_catalog.py`; add Chinese comments for why duplicate IDs, unknown fixture sets, missing files, and mixed KB IDs are rejected. Do not access PG, vector stores, or environment variables from this module.

- [ ] **Step 4: Add the unified manifest without deleting old fixtures.**

Create a manifest that maps the existing baseline files and the 100 expanded files. Keep old source paths in `source_file` during migration so the first implementation does not require destructive file moves. Add `source_compat_path` when the old fixture path must remain readable.

- [ ] **Step 5: Register `rag_eval_kb` and aliases.**

Add `rag_eval_kb` with `audience="test"`, `owner_depts=["all"]`, and a Chinese comment stating that it is synthetic evaluation data and must never enter production fallback. Keep `rag_test_kb` and `rag_100_docs` as deprecated read-only aliases for one migration cycle.

- [ ] **Step 6: Run the focused tests and commit the contract.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_fixture_catalog.py -q --no-cov`

Expected: PASS. Commit only catalog/config/manifest/test files with message `feat: add unified rag evaluation kb contract`.

### Task 2: 统一 canonical cases 与 suites

**Files:**
- Modify: `backend/evaluation/dataset/loader.py`
- Modify: `backend/evaluation/service.py`
- Modify: `backend/evaluation/datasets/rag/cases.jsonl`
- Modify: `backend/evaluation/datasets/rag_100_docs.json`
- Modify: `backend/evaluation/datasets/rag_test_kb.json`
- Create: `backend/evaluation/datasets/rag/suites/pr_baseline.json`
- Create: `backend/evaluation/datasets/rag/suites/expanded_100.json`
- Create: `backend/evaluation/datasets/rag/suites/scale_20k.json`
- Test: `backend/tests/evaluation/test_rag_eval_suites.py`

**Interfaces:**
- Suite metadata: `kb_id`, `fixture_set`, `dataset_version`, `case_ids`
- `load_dataset("rag", selection="pr_baseline")` returns cases whose metadata contains the suite KB and fixture set.

- [ ] **Step 1: Add failing suite consistency tests.**

```python
def test_pr_suite_has_canonical_cases_and_baseline_scope():
    cases = load_dataset("rag", selection="pr_baseline")
    assert cases
    assert {c.metadata["kb_id"] for c in cases} == {"rag_eval_kb"}
    assert {c.metadata["fixture_set"] for c in cases} == {"baseline"}


def test_expanded_suite_is_separate_from_pr_suite():
    baseline = {c.id for c in load_dataset("rag", selection="pr_baseline")}
    expanded = {c.id for c in load_dataset("rag", selection="expanded_100")}
    assert baseline
    assert expanded
    assert baseline.isdisjoint(expanded)
```

- [ ] **Step 2: Run the tests and verify the current loader cannot satisfy the suite metadata contract.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_rag_eval_suites.py -q --no-cov`

Expected: FAIL because the suite loader currently only resolves `case_ids` and does not propagate corpus metadata.

- [ ] **Step 3: Extend suite loading with explicit metadata.**

Update `_load_suite` to validate `kb_id`, runtime `fixture_set`, and `dataset_version`; copy these values into each returned case's metadata without mutating the canonical source object shared by another suite. Preserve an optional `source_fixture_set` on the case for annotation provenance. If a case declares a conflicting KB, raise a descriptive `ValueError` instead of silently falling back; a different runtime fixture set is allowed only for the explicit `scale_20k` suite.

- [ ] **Step 4: Convert the two existing datasets into one canonical namespace.**

Preserve existing case IDs (`RC-*` and `RD-*`). Move the authoritative copies into `rag/cases.jsonl`, add `metadata.kb_id="rag_eval_kb"` and the proper `source_fixture_set`; if duplicate IDs occur, fail with a content-diff report instead of silently choosing one. Keep legacy JSON files as compatibility inputs with a Chinese deprecation note. Do not change question text or expected facts during this migration.

- [ ] **Step 5: Create suites.**

`pr_baseline.json` references the existing small regression cases; `expanded_100.json` references the 100-document cases; `scale_20k.json` reuses the expanded questions but declares the runtime `fixture_set="scale_20k"` while retaining `source_fixture_set="expanded_100"`, so the runner requires a staging corpus without changing the expected-answer provenance. Keep `quick_26.json` as a quick subset and update its description.

- [ ] **Step 6: Run loader, dataset, and evaluation characterization tests.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_fixture_catalog.py backend/tests/evaluation/test_rag_eval_suites.py backend/tests/evaluation/test_eval_characterization.py -q --no-cov`

Expected: PASS. Commit with message `feat: unify rag evaluation cases and suites`.

### Task 3: 统一 fixture 入库和元数据传播

**Files:**
- Modify: `backend/scripts/ingest_eval_fixtures.py`
- Modify: `backend/scripts/generate_test_docs.py`
- Modify: `backend/rag/indexing/indexer.py`
- Modify: `backend/rag/indexing/doc_registry.py`
- Modify: `backend/rag/indexing/doc_registry_pg.py`
- Test: `backend/tests/rag/test_eval_fixture_ingest.py`
- Test: `backend/tests/rag/test_indexer_fixture_metadata.py`

**Interfaces:**
- `ingest_eval_fixtures.py --fixture-set baseline|expanded_100 [--include-scanned]`
- `ingest_fixture(document: FixtureDocument, *, registry, indexer) -> FixtureIngestResult`
- `FixtureDocument.metadata["fixture_set"]` is preserved in registry rows, chunk metadata, vector metadata, and BM25 metadata.

- [ ] **Step 1: Add failing tests for metadata propagation and idempotency.**

```python
def test_indexed_fixture_metadata_contains_canonical_kb_and_set(fake_indexer):
    document = FixtureDocument(
        fixture_doc_id="policy_attendance_rnd",
        source_file="md/policy_考勤管理制度_研发中心.md",
        fixture_set="baseline",
        format="md",
        kb_id="rag_eval_kb",
        metadata={},
    )
    result = ingest_fixture(document, registry=FakeRegistry(), indexer=fake_indexer)
    assert result.metadata["kb_id"] == "rag_eval_kb"
    assert result.metadata["fixture_set"] == "baseline"


def test_reingest_same_fixture_does_not_duplicate_registry_rows(fake_registry, fixture_path):
    document = fixture_document_from_path(fixture_path, fixture_set="expanded_100")
    ingest_fixture(document, registry=fake_registry, indexer=FakeIndexer())
    ingest_fixture(document, registry=fake_registry, indexer=FakeIndexer())
    assert fake_registry.count_by_fixture_doc_id("policy_attendance_rnd") == 1
```

- [ ] **Step 2: Run focused tests and confirm the old scripts do not expose fixture_set.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_eval_fixture_ingest.py backend/tests/rag/test_indexer_fixture_metadata.py -q --no-cov`

Expected: FAIL because old ingest only writes `rag_100_docs` or `rag_test_kb`.

- [ ] **Step 3: Make the ingest script catalog-driven.**

Replace hardcoded fixture selection with `--fixture-set`; validate the requested set before touching `data/docs`; use `rag_eval_kb` for all new rows; preserve the old no-argument behavior through a compatibility warning that maps to `expanded_100`.

- [ ] **Step 4: Propagate fixture metadata through registry and indexer.**

Add the fields to the existing metadata whitelist and serialization points. Keep the fields optional for production documents so existing production indexing remains backward compatible. Add Chinese comments explaining why `fixture_set` is test-only and must not affect production authorization.

- [ ] **Step 5: Preserve old commands as wrappers.**

The old `rag_test_kb` and `rag_100_docs` commands should call the new catalog-driven path with a deprecation warning and a deterministic fixture set. They must not silently write the old KB ID after the migration switch is enabled.

- [ ] **Step 6: Run indexing contract tests and commit.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_eval_fixture_ingest.py backend/tests/rag/test_indexer_fixture_metadata.py backend/tests/rag/test_index_consistency.py -q --no-cov`

Expected: PASS. Commit with message `feat: route rag fixtures through unified eval kb`.

### Task 4: 对齐检索评测和生产检索链路

**Files:**
- Modify: `backend/evaluation/runners/rag.py`
- Modify: `backend/evaluation/config.py`
- Modify: `backend/evaluation/cli.py`
- Modify: `backend/tests/evaluation/test_eval_golden.py`
- Test: `backend/tests/evaluation/test_rag_eval_runner_scope.py`

**Interfaces:**
- `EvalConfig` carries `kb_id`, `fixture_set`, `dataset_version`, and `multiquery`.
- `build_eval_scope(*, kb_id: str, fixture_set: str, multiquery: bool) -> EvalScope`
- `EvalScope.as_dict() -> dict[str, object]`
- Runner applies `fixture_set` only for `rag_eval_kb` and records the actual scope in each result.

- [ ] **Step 1: Add failing tests for scope enforcement and production-chain flags.**

```python
def test_runner_rejects_unknown_fixture_set():
    with pytest.raises(ValueError, match="fixture_set"):
        build_eval_scope(kb_id="rag_eval_kb", fixture_set="missing")


def test_runner_records_scope_and_multiquery():
    scope = build_eval_scope(
        kb_id="rag_eval_kb", fixture_set="expanded_100", multiquery=True,
    )
    assert scope.as_dict() == {
        "kb_id": "rag_eval_kb",
        "fixture_set": "expanded_100",
        "multiquery": True,
    }
```

- [ ] **Step 2: Run the focused tests and confirm scope is currently absent.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_rag_eval_runner_scope.py -q --no-cov`

Expected: FAIL because the runner currently only uses case-level `kb_id` and has no unified fixture scope object.

- [ ] **Step 3: Add a validated evaluation scope.**

Implement a small immutable scope object or validated dictionary in the evaluation layer. Reject unknown KB/fixture combinations and include scope in report metadata and per-case actual output.

- [ ] **Step 4: Apply fixture filtering to document, chunk, BM25, and ablation paths.**

Extend the existing metadata filter construction so full, vector-only, BM25-only, and hybrid ablations use the same `fixture_set` restriction. Do not duplicate filter logic; call one helper and keep the robust list/`$or` metadata behavior already used by production retrieval.

- [ ] **Step 5: Align diagnostic configuration.**

Replace the fixed Stage 1 probe constant with the configured candidate K for the selected scope, while retaining a separate explicit diagnostic K if needed. Ensure `--multiquery` and the report reflect the actual chain used.

- [ ] **Step 6: Remove the broken pytest `--full` option.**

Delete the module-local `pytest_addoption` and unused `full_results` fixture. Add a test asserting that canonical full evaluation is invoked through `python -m evaluation rag --selection expanded_100`, not an unregistered pytest option.

- [ ] **Step 7: Run runner and evaluation tests, then commit.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_rag_eval_runner_scope.py backend/tests/evaluation/test_eval_characterization.py backend/tests/evaluation/test_semantic_metrics.py -q --no-cov`

Expected: PASS. Commit with message `fix: align rag evaluation scope with production chain`.

### Task 5: 迁移验证、兼容提示和文档

**Files:**
- Modify: `backend/config/knowledge_base.py`
- Modify: `backend/scripts/ingest_eval_fixtures.py`
- Modify: `docs/rag_eval/README.md`
- Modify: `docs/rag_eval/EVALUATION_MATRIX.md`
- Modify: `docs/RAG_DESIGN.md`
- Modify: `docs/RAG-20k-上线检查清单.md`
- Test: `backend/tests/evaluation/test_rag_eval_migration.py`

**Interfaces:**
- Old KB IDs remain readable during migration but new writes resolve to `rag_eval_kb`.
- A migration report contains source KB, target KB, fixture set, rows copied, rows skipped, and consistency result.

- [ ] **Step 1: Add failing migration tests.**

```python
def test_old_kb_resolves_to_compatibility_profile():
    profile = resolve_eval_profile("rag_100_docs")
    assert profile.target_kb_id == "rag_eval_kb"
    assert profile.fixture_set == "expanded_100"


def test_production_authorized_kbs_exclude_unified_eval_kb():
    assert "rag_eval_kb" not in authorized_kbs("customer")
    assert "rag_eval_kb" not in authorized_kbs("employee", "general")
```

- [ ] **Step 2: Implement compatibility resolution and migration report.**

Keep aliases read-only. New ingest calls must resolve the target KB before copying files or writing registry rows. Return explicit counts and failures; never silently treat missing source files as successful migration.

- [ ] **Step 3: Update documentation and command examples.**

Document three commands: PR baseline, expanded_100 release/nightly, and scale_20k staging. Remove stale 37/145/Chroma statements and state that `pytest --full` is not a supported entrypoint.

- [ ] **Step 4: Run migration and authorization tests, then commit.**

Run: `D:/Python/python.exe -m pytest backend/tests/evaluation/test_rag_eval_migration.py backend/tests/evaluation/test_rag_eval_suites.py -q --no-cov`

Expected: PASS. Commit with message `docs: document rag eval kb migration and usage`.

### Task 6: 真实评测、回归和发布检查

**Files:**
- Test/verify: `backend/tests/rag/`, `backend/tests/evaluation/`, `backend/evaluation/datasets/rag/suites/`
- Reports: `data/eval_runs/<run_id>/`

- [ ] **Step 1: Run fast unit and contract regression.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag backend/tests/evaluation/test_fixture_catalog.py backend/tests/evaluation/test_rag_eval_suites.py backend/tests/evaluation/test_rag_eval_runner_scope.py -q --no-cov`

Expected: all selected tests pass; any PG-dependent skip must be reported separately.

- [ ] **Step 2: In a test-only environment, ingest baseline and verify idempotency.**

Run the unified ingest command twice for `baseline`; verify the second run does not increase active registry rows, vector rows, chunk rows, or BM25 documents.

- [ ] **Step 3: In a test-only environment, ingest expanded_100.**

Run expanded ingestion without scans first; then run `--include-scanned` only when OCR health check passes. Record active/failed/skipped counts and consistency output.

- [ ] **Step 4: Run the PR baseline evaluation.**

Run: `cd backend; D:/Python/python.exe -m evaluation rag --selection pr_baseline --no-ragas --no-resume`

Expected: report records `kb_id=rag_eval_kb`, `fixture_set=baseline`, and no cross-scope documents.

- [ ] **Step 5: Run expanded evaluation with the production retrieval flags.**

Run: `cd backend; D:/Python/python.exe -m evaluation rag --selection expanded_100 --multiquery --no-ragas --no-resume`

Expected: report includes per-tier Recall@K, MRR, NDCG, Top-1, reject accuracy, department/version isolation, scope metadata, and per-case failures.

- [ ] **Step 6: Run the project-required consistency gates.**

Run from `backend`:

```powershell
D:/Python/python.exe -m pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 7: Review generated reports and record release evidence.**

Confirm report paths, fixture counts, data version, chain flags, skipped tests, failed cases, and whether the run used OCR/live embedding/RAGAS. Do not claim 20k readiness until the separate staging capacity run has measured throughput and latency.
