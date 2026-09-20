# Model Kind Catalog Implementation Plan

> **For agentic workers:** Inline execution in the current workspace. Follow the steps task-by-task with TDD checkpoints.

**Goal:** 让供应商下的模型明确区分 `chat`、`embedding`、`rerank`，新增与切换时按模型类型筛选，并在后端阻止角色绑定到错误类型。

**Architecture:** 供应商继续负责协议、Base URL 和凭据；`llm_models` 增加模型用途类型作为统一目录事实源。通用文本角色和专项角色都引用该目录，协议适配器根据模型类型选择测试端点；旧的无类型数据库模型向后兼容回填为 `chat`。

**Tech Stack:** FastAPI/Pydantic、PostgreSQL Alembic、Python registry cache、Next.js/React/TypeScript、Vitest、pytest。

**Spec:** `docs/model-config-governance-design.md` 与本轮已确认的三类模型设计。

## Global Constraints

- API Key 只能加密存储，前端只显示配置状态、末四位和指纹，禁止回显明文。
- DB 配置优先于 env；DB 未配置的字段才回落 env，DB 不可用时保留上一次已知覆盖并 fail-open 到代码层/env。
- `chat`、`embedding`、`rerank` 是模型用途类型，不与 `openai`、`anthropic`、`ollama` 协议混淆。
- 保存前必须按模型类型执行对应最小连接测试；测试失败不得新增或启用模型。
- 不修改无关业务链路；保留现有供应商 CRUD、专项模型配置和探测错误展示能力。

---

### Task 1: 模型类型领域契约与数据库迁移

**Files:**
- Create: `backend/sql/alembic/memory/versions/0020_model_kind_catalog.py`
- Modify: `backend/infra/llm/models.py`
- Modify: `backend/infra/llm/registry_store.py`
- Modify: `backend/tests/infra/test_llm_role_resolution.py`
- Create: `backend/tests/infra/test_model_kind_catalog.py`

**Interfaces:**
- Produces `MODEL_KINDS = {"chat", "embedding", "rerank"}`.
- Produces `normalize_model_kind(value) -> str` and `role_model_kind(role) -> str`.
- `get_available_models()` entries expose `model_kind`, defaulting legacy entries to `chat`.

- [ ] Write failing tests for legacy default, DB model kind preservation, and role/type compatibility.
- [ ] Run `D:/Python/python.exe -m pytest backend/tests/infra/test_model_kind_catalog.py -q --no-cov` and verify the new assertions fail.
- [ ] Add the `model_kind` column with `chat` default and a check constraint; backfill existing rows.
- [ ] Make registry loading and model merging carry `model_kind` without changing existing provider resolution.
- [ ] Implement role compatibility helpers and reject `embedding`/`rerank` models for chat roles and vice versa.
- [ ] Re-run the focused tests and verify they pass.

### Task 2: 后端模型登记、类型过滤与按类型测试

**Files:**
- Modify: `backend/app/api/routes/model_config.py`
- Modify: `backend/app/api/routes/sys_providers.py`
- Modify: `backend/services/model_config.py`
- Modify: `backend/services/provider_probe.py`
- Modify: `backend/tests/api/test_model_config_write_api.py`
- Modify: `backend/tests/api/test_sys_providers_probe_api.py`

**Interfaces:**
- `ProviderCreateRequest` accepts `modelKind` with default `chat` for backward compatibility.
- Add `POST /sys/providers/{provider_id}/models` and `GET /sys/providers/{provider_id}/models?modelKind=...`.
- Add `modelKind` to draft probe input and select the probe contract from it.

- [ ] Add failing API tests for creating a model of each kind, filtering by kind, and rejecting incompatible role bindings.
- [ ] Run the focused API tests and confirm they fail before implementation.
- [ ] Implement model registration as a provider-scoped catalog row; do not create a second provider for each model.
- [ ] Route chat probes to the existing chat probe, embedding probes to an embedding request, and rerank probes to a rerank request.
- [ ] Require a successful draft probe before creating a new model; preserve the existing edit flow and encrypted credential handling.
- [ ] Re-run focused backend tests.

### Task 3: 管理端按类型新增、编辑与角色切换

**Files:**
- Modify: `frontend-admin/src/api/modelConfig.ts`
- Modify: `frontend-admin/src/types/modelConfig.ts`
- Modify: `frontend-admin/src/components/model-config/ProvidersTab.tsx`
- Modify: `frontend-admin/src/components/model-config/RoleBindingsTab.tsx`
- Modify: `frontend-admin/src/components/model-config/ProvidersTab.test.tsx`
- Modify: `frontend-admin/src/types/modelConfig.test.ts`

**Interfaces:**
- `ModelKind = 'chat' | 'embedding' | 'rerank'`.
- Provider model rows expose `modelKind` and type label.
- Role catalog requests may filter by `modelKind` and render only compatible entries.

- [ ] Add failing UI tests for type selection, grouped provider model display, and role-specific filtering.
- [ ] Run the focused Vitest files and verify the new assertions fail.
- [ ] Add a model-kind selector to the provider modal; text/embedding/rerank use distinct labels and validation copy.
- [ ] Display provider models grouped by kind and show the kind beside every model name.
- [ ] Make role binding dropdowns filter by expected kind; show a type mismatch reason if current legacy data is invalid.
- [ ] Preserve masked keys, test duration, failure details, and “test then save” behavior.
- [ ] Re-run Vitest and `npx tsc --noEmit`.

### Task 4: 文档、运行时验收与回归

**Files:**
- Modify: `docs/model-config-governance-progress-report-2026-09-19.md`
- Modify: `docs/model-config-admin-ui-design.md`
- Modify: `backend/tests/evaluation/test_eval_model_routing.py`
- Modify: `backend/tests/infra/test_model_config_runtime.py`

- [ ] Document the provider/model split, model kinds, type-specific tests, and DB/env precedence.
- [ ] Add runtime assertions that a DB model kind is used after registry refresh and that a wrong-kind role fails with an actionable message.
- [ ] Run backend focused tests, frontend focused tests, TypeScript compilation, Python compilation, and `git diff --check`.
- [ ] Rebuild/restart the affected services and verify the browser shows grouped models and the correct role options.
