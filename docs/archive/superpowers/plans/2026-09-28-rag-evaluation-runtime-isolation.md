# RAG Evaluation Runtime Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 RAGPipeline 的索引构建、在线检索和评估检索隔离，保证评估 CLI 冷启动只读既有索引并输出真实 RAG 指标。

**Architecture:** 在现有 `RAGPipeline` 内增加显式 runtime mode，复用已有 vector/BM25 加载和索引器，不重写 RAG 链。index 模式保留现有构建路径；runtime/evaluation 直接加载持久化索引，evaluation 额外禁止任何重建并绑定固定 snapshot。fixture 导入通过现有 `ingest_eval_fixtures` 能力提供薄模块入口，避免第二套索引逻辑。

**Tech Stack:** Python 3、FastAPI/LangChain RAG、pgvector、BM25Store、pytest、现有 evaluation CLI。

**Spec:** `docs/superpowers/specs/2026-09-28-rag-evaluation-runtime-isolation.md`

## Global Constraints

- 不修改 Router、LangGraph 节点、SSE、checkpoint、embedding 模型或生产聊天协议。
- `evaluation` 初始化禁止扫描 `data/docs`、增量 sync、metadata LLM、向量写入和 BM25 重建。
- `runtime` 初始化禁止隐式同步和索引重建；查询仍可使用 query embedding。
- fixture chunk metadata 必须包含 `doc_id`、`kb_id`、`fixture_set`、`dataset`、`version_id`。
- 局部 pytest 命令必须带 `--no-cov`。
- 保留工作区已有三个前端 tsconfig 改动，不暂存、不修改。

## Review Focus

- 评估索引缺失或 BM25 失配：必须直接失败并说明需要先导入 fixture，不能回退扫描或重建；测试归 Task 2。
- runtime/evaluation 的 `get_embedding()`：允许查询 embedding，但不得触发写入；测试归 Task 2。
- 无参 `RAGPipeline()` 的存量调用：必须保持兼容，线上 singleton/评估 runner 必须显式 mode；测试归 Task 1。
- fixture 旧 registry 行缺少 `fixture_set`/`version_id`：导入必须覆盖 metadata，不能只更新 catalog；测试归 Task 3。
- BM25 持久化索引缺失：evaluation 不得用 `self.docs` 兜底加载目录；测试归 Task 2。

### Task 1: 增加 RAGPipeline 运行模式边界

**Files:**
- Modify: `backend/rag/pipeline.py:67-160,214-285,452-505`
- Modify: `backend/rag/pipeline.py:1138-1185`
- Modify: `backend/evaluation/runners/_common.py:98-118`
- Test: `backend/tests/rag/test_pipeline_runtime_modes.py`
- Test: `backend/tests/evaluation/test_rag_pipeline_evaluation_mode.py`

**Interfaces:**
- `RAGPipeline(mode: str = "index")` 接受 `index`、`runtime`、`evaluation`，未知值抛 `ValueError`。
- `_prepare_vector_store()` 在 `runtime/evaluation` 只调用 `_load_existing_db()`，不调用 `_init_vector_dbs_incremental()` 或 `_init_vector_dbs_full()`。
- `_init_retrievers()` 在 `runtime/evaluation` 加载 BM25 后若不存在、过期或 hash/文件集不匹配，抛 `RuntimeError`，不调用 `BM25Store.build()`，不调用 `_ensure_docs_loaded()`。
- `get_rag_pipeline()` 的本地 singleton 显式构造 `RAGPipeline(mode="runtime")`。
- `backend.evaluation.runners._common.init_rag_pipeline()` 显式构造 `RAGPipeline(mode="evaluation")`。

- [ ] **Step 1: Write the failing tests**

```python
def test_evaluation_mode_does_not_sync(monkeypatch):
    from backend.rag import pipeline as module

    calls = []
    monkeypatch.setattr(module, "get_embedding", lambda: object())
    monkeypatch.setattr(module.RAGPipeline, "_load_existing_db", lambda self, path, kind: object())
    monkeypatch.setattr(module.RAGPipeline, "_init_vector_dbs_incremental", lambda self: calls.append("sync"))
    monkeypatch.setattr(module.RAGPipeline, "_init_retrievers", lambda self: None)
    module.RAGPipeline(mode="evaluation")
    assert calls == []

def test_runtime_mode_never_builds_bm25(monkeypatch):
    from backend.rag import pipeline as module

    pipeline = module.RAGPipeline.__new__(module.RAGPipeline)
    pipeline.mode = "runtime"
    pipeline.embedding = object()
    pipeline.vectordb = type("Vector", (), {"get": lambda self: {"ids": ["c1"], "documents": ["text"], "metadatas": [{}]}})()
    pipeline.doc_db = object()
    monkeypatch.setattr(module, "BM25Store", lambda: type("Store", (), {
        "load": lambda self, k: None,
        "build": lambda self, *args, **kwargs: (_ for _ in ()).throw(AssertionError("write")),
    })())
    with pytest.raises(RuntimeError, match="BM25"):
        pipeline._init_retrievers()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; D:/Python/python.exe -m pytest tests/rag/test_pipeline_runtime_modes.py tests/evaluation/test_rag_pipeline_evaluation_mode.py -q --no-cov`

Expected: FAIL because `RAGPipeline` does not accept `mode` and evaluation runner still constructs the default pipeline.

- [ ] **Step 3: Implement the minimal mode-aware initialization**

Add a validated `mode` field in `__init__`, branch `_prepare_vector_store()` before incremental/full index paths, and add a read-only BM25 branch that only loads persisted data. In read-only modes, use the existing vector store as the BM25 corpus only for consistency checks; never call `_ensure_docs_loaded()` or `build()`. Raise `RuntimeError("evaluation/runtime 只读索引不可用: ...")` with the failing condition. Keep index mode behavior unchanged.

Update the two call sites to pass explicit modes. Do not change query methods or RAGChain construction.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; D:/Python/python.exe -m pytest tests/rag/test_pipeline_runtime_modes.py tests/evaluation/test_rag_pipeline_evaluation_mode.py tests/rag/test_person_index_initialization.py tests/test_pipeline_init_nonblocking.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/rag/pipeline.py backend/evaluation/runners/_common.py backend/tests/rag/test_pipeline_runtime_modes.py backend/tests/evaluation/test_rag_pipeline_evaluation_mode.py
git commit -m "feat(rag): isolate runtime and evaluation pipeline modes" -- backend/rag/pipeline.py backend/evaluation/runners/_common.py backend/tests/rag/test_pipeline_runtime_modes.py backend/tests/evaluation/test_rag_pipeline_evaluation_mode.py
```

### Task 2: 固定评估 snapshot 并阻止错误范围读取

**Files:**
- Create: `backend/evaluation/datasets/rag/snapshots/baseline.json`
- Modify: `backend/evaluation/runners/_common.py`
- Modify: `backend/evaluation/datasets/rag/manifest.json`
- Test: `backend/tests/evaluation/test_rag_eval_snapshot.py`

**Interfaces:**
- `load_rag_snapshot("baseline") -> dict` 返回 `kb_id`、`fixture_set`、`version_id`、`cases_path`。
- `init_rag_pipeline()` 只负责建立 evaluation pipeline；snapshot 由评估 scope/runner 读取并作为 metadata filter 输入。

- [ ] **Step 1: Write the failing tests**

```python
def test_baseline_snapshot_has_stable_scope():
    snapshot = load_rag_snapshot("baseline")
    assert snapshot["kb_id"] == "rag_eval_kb"
    assert snapshot["fixture_set"] == "baseline"
    assert snapshot["version_id"]

def test_unknown_snapshot_fails_without_directory_fallback():
    with pytest.raises(ValueError, match="snapshot"):
        load_rag_snapshot("does-not-exist")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; D:/Python/python.exe -m pytest tests/evaluation/test_rag_eval_snapshot.py -q --no-cov`

Expected: FAIL because no snapshot loader/file exists.

- [ ] **Step 3: Implement snapshot loader and scope binding**

Create a small pure loader under `backend/evaluation/datasets/rag/snapshots.py` that reads the named JSON under the package directory, validates non-empty `kb_id`, `fixture_set`, `version_id`, and rejects unknown names. Add `baseline.json` with the existing baseline cases path and the canonical KB. Thread `version_id` into the existing evaluation metadata filter without changing the public CLI flags.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; D:/Python/python.exe -m pytest tests/evaluation/test_rag_eval_snapshot.py tests/evaluation/test_rag_eval_runner_scope.py tests/evaluation/test_rag_eval_suites.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/evaluation/datasets/rag/snapshots.py backend/evaluation/datasets/rag/snapshots/baseline.json backend/evaluation/runners/_common.py backend/evaluation/datasets/rag/manifest.json backend/tests/evaluation/test_rag_eval_snapshot.py
git commit -m "feat(evaluation): bind rag benchmark to immutable snapshot" -- backend/evaluation/datasets/rag/snapshots.py backend/evaluation/datasets/rag/snapshots/baseline.json backend/evaluation/runners/_common.py backend/evaluation/datasets/rag/manifest.json backend/tests/evaluation/test_rag_eval_snapshot.py
```

### Task 3: 补齐 fixture metadata 并提供 baseline 导入入口

**Files:**
- Create: `backend/evaluation/import_fixture.py`
- Modify: `backend/scripts/ingest_eval_fixtures.py`
- Modify: `backend/rag/indexing/indexer.py:64-71,1098-1115,1390-1412,1460-1485,1735-1755`
- Test: `backend/tests/rag/test_eval_fixture_metadata_contract.py`
- Test: `backend/tests/evaluation/test_import_fixture_entrypoint.py`

**Interfaces:**
- `python -m backend.evaluation.import_fixture baseline` delegates to the existing importer with an explicit `--fixture-set baseline` and returns its exit code.
- `_apply_fixture_metadata()` adds `fixture_set` and, for canonical evaluation KB rows, `dataset="rag_eval"` without changing production rows.
- `IncrementalIndexer._index_file` metadata path preserves registry `version_id` and writes it to every chunk.

- [ ] **Step 1: Write the failing tests**

```python
def test_fixture_metadata_contains_full_evaluation_identity():
    metadata = build_fixture_chunk_metadata(
        {"doc_id": "refund_policy", "kb_id": "rag_eval_kb", "fixture_set": "baseline", "version_id": "baseline-v1"}
    )
    assert metadata == {
        "doc_id": "refund_policy",
        "kb_id": "rag_eval_kb",
        "fixture_set": "baseline",
        "dataset": "rag_eval",
        "version_id": "baseline-v1",
    }

def test_import_fixture_delegates_to_canonical_fixture_set(monkeypatch):
    seen = []
    monkeypatch.setattr("backend.scripts.ingest_eval_fixtures.main", lambda: seen.append(sys.argv[:]) or 0)
    assert import_fixture.main(["baseline"]) == 0
    assert "--fixture-set" in seen[0] and "baseline" in seen[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; D:/Python/python.exe -m pytest tests/rag/test_eval_fixture_metadata_contract.py tests/evaluation/test_import_fixture_entrypoint.py -q --no-cov`

Expected: FAIL because chunk metadata lacks the dataset contract and the module entrypoint does not exist.

- [ ] **Step 3: Implement metadata contract and thin CLI**

Add one pure metadata helper in the existing indexer path and call it for document/chunk metadata. Use registry `version_id` as the authoritative version; preserve empty string for non-versioned production documents. Ensure fixture imports instantiate the indexer in `mode="index"` through a dedicated explicit pipeline path, not `get_rag_pipeline()` runtime singleton. Add the module `main(argv: list[str] | None = None)` that temporarily supplies `--fixture-set <name>` to the existing importer and restores `sys.argv` in `finally`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; D:/Python/python.exe -m pytest tests/rag/test_eval_fixture_metadata_contract.py tests/rag/test_eval_fixture_ingest.py tests/rag/test_indexer_fixture_metadata.py tests/evaluation/test_import_fixture_entrypoint.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/evaluation/import_fixture.py backend/scripts/ingest_eval_fixtures.py backend/rag/indexing/indexer.py backend/tests/rag/test_eval_fixture_metadata_contract.py backend/tests/evaluation/test_import_fixture_entrypoint.py
git commit -m "feat(evaluation): add baseline fixture import and metadata identity" -- backend/evaluation/import_fixture.py backend/scripts/ingest_eval_fixtures.py backend/rag/indexing/indexer.py backend/tests/rag/test_eval_fixture_metadata_contract.py backend/tests/evaluation/test_import_fixture_entrypoint.py
```

### Task 4: Router 回归、真实 fixture 导入与 RAG smoke

**Files:**
- Modify: `docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Router_Consolidation_Report.md`
- Create: `docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Evaluation-Runtime-Report.md` (if the existing report is kept immutable)

- [ ] **Step 1: Run focused Router regression**

Run: `cd backend; D:/Python/python.exe -m pytest tests/orchestration tests/evaluation/test_cli_registry_bootstrap.py -q --no-cov`

Expected: PASS, with no Router/SSE/checkpoint file changes.

- [ ] **Step 2: Import the baseline fixture**

Run: `D:/Python/python.exe -m backend.evaluation.import_fixture baseline`

Expected output includes `fixture import success`, document/chunk counts, `kb_id=rag_eval_kb`, `fixture_set=baseline`, and a collection/version identifier. If an existing index is absent, the command may perform the explicitly requested offline index build; it must not run through evaluation mode.

- [ ] **Step 3: Validate metadata and run real smoke**

Run: `D:/Python/python.exe -m backend.evaluation.cli --smoke --no-ragas --selection pr_baseline --no-resume`

Expected: cold start constructs `RAGPipeline(mode="evaluation")` without `sync`/document scan; report contains `Recall@5`, `MRR`, and `NDCG` with non-error counts. Capture the generated report path and the evaluation run log showing zero index-sync calls.

- [ ] **Step 4: Update STOP B report**

Record Router test command/results, fixture import command/results, evaluation mode and read-only evidence, embedding load status, metadata identity sample, and real metric values. Set `ROUTER_CONSOLIDATION_PASS=true` only if all five acceptance conditions pass; otherwise record the exact remaining blocker.

- [ ] **Step 5: Run final verification**

Run: `cd backend; D:/Python/python.exe -m py_compile rag/pipeline.py rag/indexing/indexer.py evaluation/import_fixture.py evaluation/datasets/rag/snapshots.py; D:/Python/python.exe -m pytest tests/rag/test_pipeline_runtime_modes.py tests/evaluation/test_rag_pipeline_evaluation_mode.py tests/evaluation/test_rag_eval_snapshot.py tests/rag/test_eval_fixture_metadata_contract.py tests/evaluation/test_import_fixture_entrypoint.py tests/orchestration tests/evaluation/test_cli_registry_bootstrap.py -q --no-cov`

Expected: all focused tests pass; no frontend tsconfig files are staged.

- [ ] **Step 6: Commit report and final implementation**

```powershell
git add -- docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Router_Consolidation_Report.md docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Evaluation-Runtime-Report.md
git commit -m "docs(evaluation): record STOP B runtime isolation verification" -- docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Router_Consolidation_Report.md docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Evaluation-Runtime-Report.md
```
