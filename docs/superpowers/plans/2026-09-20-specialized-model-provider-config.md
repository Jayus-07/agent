# 通用专项模型供应商配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 embedding/rerank 通过管理端配置可扩展供应商、协议适配器、加密凭据和模型绑定，并让运行时优先使用数据库配置。

**Architecture:** 复用现有 `llm_providers` 与 `llm_provider_credentials` 保存供应商元数据和 Fernet 密文，新增专项角色绑定表记录 `embedding`/`rerank` 的供应商、适配器、模型和端点。后端通过适配器注册表执行测试和运行时调用，数据库配置刷新到进程内缓存后由向量化与重排链路读取；没有专项 DB 配置时保留现有 `.env` 兜底。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy async session、PostgreSQL/Alembic、httpx、LangChain OpenAI Embeddings、Next.js 14、React、Vitest/Jest。

**Spec:** `docs/model-config-governance-design.md` 与本轮已确认的“供应商 + 协议适配器 + 角色绑定”方案。

## Global Constraints

- API Key 只允许写入 Fernet 密文，任何读接口、历史和日志不得返回明文或密文。
- 专项测试使用固定最小请求，保存/启用仅在所选能力测试通过后发生；测试结果必须包含耗时和可操作错误摘要。
- `embedding` 模型变更必须返回并展示全量重建索引提示；维度相同也不能跳过该提示。
- 适配器由后端白名单注册，前端只选择协议/供应商，不感知请求体和响应格式。
- 现有未提交改动属于用户工作，所有编辑只触及本计划列出的文件和新增文件。
- 局部 pytest 命令必须带 `--no-cov`；Python 修改后运行 `py_compile`，前端修改后运行 `tsc` 与相关测试。

### Task 1: 专项绑定数据模型与运行时缓存

**Files:**
- Create: `backend/sql/alembic/memory/versions/0019_specialized_model_bindings.py`
- Create: `backend/infra/llm/specialized.py`
- Modify: `backend/infra/llm/registry_store.py`
- Test: `backend/tests/infra/test_specialized_runtime.py`

**Interfaces:**
- `specialized.set_bindings(bindings: Mapping[str, Mapping[str, Any]] | None) -> None`
- `specialized.resolve_binding(role: str) -> SpecializedBinding | None`
- `RegistrySnapshot.specialized: dict[str, dict[str, Any]]`

- [ ] **Step 1: Write the failing test**

```python
def test_specialized_binding_is_runtime_resolved_without_exposing_key():
    specialized.set_bindings({
        "embedding": {
            "provider_id": "dashscope-rag",
            "adapter": "dashscope_embedding",
            "model_name": "qwen3.7-text-embedding",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "options": {"dimensions": 1024},
            "enabled": True,
        }
    })
    binding = specialized.resolve_binding("embedding")
    assert binding is not None
    assert binding.model_name == "qwen3.7-text-embedding"
    assert "key" not in repr(binding).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:/Python/python.exe -m pytest backend/tests/infra/test_specialized_runtime.py -q --no-cov`

Expected: FAIL because the specialized runtime module and binding type do not exist.

- [ ] **Step 3: Write minimal implementation**

Create an immutable binding dataclass containing only role, provider id, adapter, model name, base URL, options, enabled and last probe metadata. Add migration 0019 with `llm_specialized_model_bindings` and extend the provider driver check with `specialized`. Load the table in `registry_store.load_registry`, include it in the runtime signature, inject it on refresh, and clear it in `reset_for_tests`; do not select any secret column from the binding table.

- [ ] **Step 4: Run test to verify it passes**

Run: `D:/Python/python.exe -m pytest backend/tests/infra/test_specialized_runtime.py -q --no-cov`

Expected: PASS.

### Task 2: 协议适配器与测试连接服务

**Files:**
- Create: `backend/services/specialized_model_probe.py`
- Create: `backend/services/specialized_model_adapters.py`
- Modify: `backend/services/model_config.py`
- Create: `backend/tests/services/test_specialized_model_probe.py`
- Create: `backend/tests/services/test_specialized_model_config.py`

**Interfaces:**
- `probe_specialized(role: Literal["embedding", "rerank"], adapter: str, base_url: str, api_key: str, model_name: str, options: Mapping[str, Any] | None = None) -> SpecializedProbeResult`
- `ModelConfigService.configure_specialized(payload: Mapping[str, Any], operator: str) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Cover these behaviors with boundary HTTP mocks only: `dashscope_embedding` sends a single input and `dimensions`; `dashscope_rerank` sends a query plus two documents; `jina_rerank` uses `/rerank`; a non-2xx response returns `ok=False`, elapsed milliseconds and a truncated provider error; `configure_specialized` performs all requested probes before opening the database transaction and leaves the database untouched when one probe fails; a successful configuration stores encrypted credentials and returns only `last4`/fingerprint plus probe summaries.

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/Python/python.exe -m pytest backend/tests/services/test_specialized_model_probe.py backend/tests/services/test_specialized_model_config.py -q --no-cov`

Expected: FAIL because no specialized adapter/probe/configuration service exists.

- [ ] **Step 3: Write minimal implementation**

Implement a backend adapter registry with explicit adapters:

```python
ADAPTERS = {
    "dashscope_embedding": DashScopeEmbeddingAdapter,
    "openai_embedding": OpenAIEmbeddingAdapter,
    "dashscope_rerank": DashScopeRerankAdapter,
    "jina_rerank": JinaRerankAdapter,
}
```

Each adapter owns URL joining, bearer authentication, fixed probe body and response validation. `configure_specialized` accepts a shared provider plus one or both role bindings, resolves an existing key when the edit form leaves `apiKey` blank, probes every requested role first, then creates/updates a `specialized` provider, upserts its encrypted credential, upserts model rows, upserts role bindings, writes redacted history and commits once. A failed probe returns `{ok: false, saved: false, tests: [...]}` and performs no write.

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:/Python/python.exe -m pytest backend/tests/services/test_specialized_model_probe.py backend/tests/services/test_specialized_model_config.py -q --no-cov`

Expected: PASS.

### Task 3: 管理端 API 与运行时接线

**Files:**
- Modify: `backend/app/api/routes/model_config.py`
- Modify: `backend/app/api/router.py` only if the existing model config router is not already registered
- Modify: `backend/rag/embedding_singleton.py`
- Modify: `backend/rag/reranker.py`
- Test: `backend/tests/api/test_model_config_write_api.py`
- Test: `backend/tests/infra/test_model_config_runtime.py`

**Interfaces:**
- `GET /api/sys/specialized-models`
- `POST /api/sys/specialized-models/test-and-save`
- Request fields: `provider.displayName`, `provider.baseUrl`, `provider.adapter`, optional `provider.providerId`, optional `provider.apiKey`, and `bindings.embedding`/`bindings.rerank` with `modelName`, `adapter`, `baseUrl`, `options`.

- [ ] **Step 1: Write the failing tests**

Assert the new GET response contains no secret fields, the POST requires admin and `Idempotency-Key`, failure returns `saved=false` with per-role elapsed/error details, success delegates to `configure_specialized`, and the main API router exposes both paths. Runtime tests inject a binding and assert embedding resolves its DB provider/model/base URL while missing bindings preserve environment behavior; rerank tests assert the selected adapter and model are passed to the backend.

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/Python/python.exe -m pytest backend/tests/api/test_model_config_write_api.py backend/tests/infra/test_model_config_runtime.py -q --no-cov`

Expected: FAIL because the routes and runtime binding hooks do not exist.

- [ ] **Step 3: Write minimal implementation**

Add Pydantic request/response models and admin-only endpoints. Extend `embedding_singleton` to resolve the specialized binding and provider credentials at construction time, using `OpenAIEmbeddings` for the OpenAI-compatible embedding adapters and passing the configured dimension. Extend `DashScopeReranker` to accept instance-level `api_format`, `model`, `base_url` and `api_key`, while preserving the existing environment fallback when no binding is present. Invalidate existing embedding/rerank singleton instances after registry refresh.

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:/Python/python.exe -m pytest backend/tests/api/test_model_config_write_api.py backend/tests/infra/test_model_config_runtime.py -q --no-cov`

Expected: PASS.

### Task 4: 管理端专项配置界面

**Files:**
- Modify: `frontend-admin/src/api/modelConfig.ts`
- Modify: `frontend-admin/src/types/modelConfig.ts`
- Modify: `frontend-admin/src/components/model-config/RoleBindingsTab.tsx`
- Modify: `frontend-admin/src/app/settings/models/page.tsx`
- Create: `frontend-admin/src/components/model-config/SpecializedModelsCard.tsx`
- Test: `frontend-admin/src/api/modelConfig.test.ts`
- Create: `frontend-admin/src/components/model-config/SpecializedModelsCard.test.tsx`

**Interfaces:**
- `listSpecializedModels(): Promise<SpecializedModelResponse>`
- `testAndSaveSpecialized(input: SpecializedModelConfigureInput): Promise<SpecializedConfigureResponse>`

- [ ] **Step 1: Write the failing tests**

Assert the API maps `elapsed_ms` to `elapsedMs`, the UI renders embedding/rerank model names and masked provider key state, blank edit keys are allowed to reuse the stored key, a failed test keeps the modal open and shows the backend reason plus milliseconds, a successful test closes and refreshes, and changing embedding displays the reindex confirmation.

- [ ] **Step 2: Run tests to verify they fail**

Run: `Set-Location frontend-admin; npx vitest run src/api/modelConfig.test.ts src/components/model-config/SpecializedModelsCard.test.tsx`

Expected: FAIL because the API types and component do not exist.

- [ ] **Step 3: Write minimal implementation**

Add an admin-only “专项模型” card to the roles tab. The edit modal has provider name, adapter/protocol, Base URL with protocol defaults, one masked API Key input, embedding model, rerank model, and “测试并保存”. Render each role’s last result with pass/fail, elapsed time and failure detail. Never populate the key input from API data. Keep the existing generic role editor for OCR/eval_gen and retain the existing reindex confirmation for embedding.

- [ ] **Step 4: Run tests to verify they pass**

Run: `Set-Location frontend-admin; npx vitest run src/api/modelConfig.test.ts src/components/model-config/SpecializedModelsCard.test.tsx`

Expected: PASS.

### Task 5: 集成验证与文档

**Files:**
- Modify: `docs/model-config-governance-progress-report-2026-09-19.md`
- Modify: `docs/model-config-governance-design.md` only for the new generic binding/adapter contract

- [ ] **Step 1: Run backend syntax and focused tests**

Run: `D:/Python/python.exe -m py_compile backend/infra/llm/specialized.py backend/services/specialized_model_adapters.py backend/services/specialized_model_probe.py backend/app/api/routes/model_config.py backend/rag/embedding_singleton.py backend/rag/reranker.py` and then the focused pytest commands from Tasks 1–3 with `--no-cov`.

- [ ] **Step 2: Run frontend typecheck/build**

Run: `Set-Location frontend-admin; npx tsc --noEmit; npm run build`

- [ ] **Step 3: Apply migrations and restart only the affected services**

Run: `D:/Program Files/workplace/agent/devctl.bat restart backend /y` after the memory migration is available; keep the existing database and unrelated dirty files unchanged.

- [ ] **Step 4: Verify through the browser**

Open `/settings/models?tab=roles`, configure the generic DashScope provider with the user-supplied key without recording it in screenshots or output, enter `qwen3.7-text-embedding` and `qwen3.7-text-rerank`, click “测试并保存”, verify per-role elapsed time and pass/fail details, refresh the page, verify the masked key and DB source, and verify that the embedding row shows the reindex warning.
