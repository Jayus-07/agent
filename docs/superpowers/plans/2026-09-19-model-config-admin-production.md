# 模型配置管理端上线闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已有模型治理骨架接成可上线的管理端闭环：管理员可查看/修改模型角色、供应商连接参数与凭据，配置有审计和脱敏，运行时刷新真正生效，管理端页面可操作并通过生产构建。

**Architecture:** 以 `llm_model_roles`、`llm_providers`、`llm_provider_credentials` 和 `llm_config_history` 为记忆库配置源；服务层负责校验、事务、加密和审计，`registry_store` 负责 15 秒热刷新，路由层只处理权限和 HTTP 契约。管理端使用单一 `api/modelConfig.ts` 和 `/settings/models` 页面，角色/供应商/历史/漂移/价格分 tab 展示；价格治理继续复用现有 `/admin/model-prices/*` 流程。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy async session、Alembic/PostgreSQL、Fernet、Next.js 14、React Query、Vitest、TypeScript。

**Spec:** `docs/model-config-governance-design.md`、`docs/model-config-admin-ui-design.md`、`docs/model-config-governance-progress-report-2026-09-19.md`

## Global Constraints

- APISIX 仍是唯一入口；新增 `/sys/*` 路由必须注册到 `backend/app/api/router.py`。
- 新增治理 API 使用裸 dict 响应，不增加 `Result` 壳。
- 管理端写操作仅允许 JWT `admin`；editor 只能访问体检/漂移只读视图。
- 密钥只在写请求中出现，入库前 Fernet 加密；任何响应、日志、历史表不得出现明文或密文。
- provider driver 只能是 `openai`、`anthropic`、`ollama`；未知驱动拒绝保存。
- 模型名大小写敏感；角色模型必须来自当前代码/DB 模型清单，embedding 变更必须返回 `requiresReindex`。
- 保留工作区已有并发改动；不执行 `git reset`、`git checkout`、递归删除或未获授权的生产迁移。
- Python 测试命令必须带 `--no-cov`；代码注释使用中文。

---

### Task 1: 先锁定后端配置持久化契约

**Files:**
- Create: `backend/tests/services/test_model_config_service.py`
- Create: `backend/tests/api/test_model_config_write_api.py`
- Modify: `backend/sql/alembic/memory/versions/0017_llm_providers.py`
- Create: `backend/services/model_config.py`

**Interfaces:**
- `ModelConfigService.list_roles() -> dict`
- `ModelConfigService.update_role(role, model_name, operator) -> dict`
- `ModelConfigService.update_provider(provider_id, payload, operator) -> dict`
- `ModelConfigService.rotate_credential(provider_id, api_key, operator) -> dict`
- `ModelConfigService.list_history(limit, object_type) -> list[dict]`
- `ModelConfigService.list_drift() -> list[dict]`
- `ModelConfigService.rollback(history_id, operator) -> dict`

- [ ] **Step 1: Write failing service tests** for role upsert, unknown role/model rejection, provider driver validation, credential fingerprint/last4-only history, and rollback refusal for credentials.
- [ ] **Step 2: Run the service tests** with `D:/Python/python.exe -m pytest tests/services/test_model_config_service.py --no-cov -q`; verify they fail because the service and tables are absent.
- [ ] **Step 3: Extend migration 0017** with role bindings, unified non-secret history, provider seed rows, and indexes; keep `down_revision = "0016"`.
- [ ] **Step 4: Implement `backend/services/model_config.py`** with parameterized SQL, Fernet encryption, transaction boundaries, model/provider validation, and redacted audit records.
- [ ] **Step 5: Re-run service tests** and verify the focused suite passes.

### Task 2: 接通运行时刷新和后端 HTTP 路由

**Files:**
- Modify: `backend/infra/llm/registry_store.py`
- Modify: `backend/config/model_roles.py`
- Modify: `backend/infra/llm/factory.py`
- Modify: `backend/infra/llm/proxy.py`
- Modify: `backend/app/api/routes/sys_providers.py`
- Modify: `backend/app/api/routes/sys_model_roles.py`
- Create: `backend/app/api/routes/model_config.py`
- Modify: `backend/app/api/router.py`
- Create: `backend/tests/api/test_model_config_routes.py`

**Interfaces:**
- `GET /sys/model-roles`
- `PUT /sys/model-roles/{role}`
- `GET /sys/providers`
- `PUT /sys/providers/{provider_id}`
- `PUT /sys/providers/{provider_id}/credential`
- `POST /sys/providers/{provider_id}/verify`
- `POST /sys/providers/verify-draft`
- `GET /sys/config/history`
- `POST /sys/config/history/{history_id}/rollback`
- `GET /sys/config/drift`

- [ ] **Step 1: Write failing route tests** asserting all routes are present in the real `api_router`, admin/editor permissions, request validation, masked responses, and no secret in history.
- [ ] **Step 2: Run the route tests** and verify failure specifically shows missing registration/write handlers.
- [ ] **Step 3: Register the routers** after the existing system routes without removing concurrent budget/price/idempotency registrations.
- [ ] **Step 4: Extend registry refresh** to load role bindings and inject them through `model_roles.inject_overrides`; invalidate model instances after credential/role changes.
- [ ] **Step 5: Make the default chat path resolve the current `main` role at call time** while preserving request-level overrides and legacy fallback semantics.
- [ ] **Step 6: Implement route handlers** using `require_admin_user` for writes/provider data, editor-level auth for drift, Pydantic length/enum constraints, and explicit `503` when the registry tables are unavailable.
- [ ] **Step 7: Re-run route/service tests** and verify the production `api_router` path inventory contains every endpoint.

### Task 3: 管理端 API 契约和导航入口

**Files:**
- Create: `frontend-admin/src/api/modelConfig.ts`
- Create: `frontend-admin/src/api/modelConfig.test.ts`
- Modify: `frontend-admin/src/components/layout/navConfig.tsx`
- Modify: `frontend-admin/src/components/layout/navConfig.test.ts`
- Modify: `frontend-admin/src/app/cost-governance/prices/page.tsx`

- [ ] **Step 1: Write failing Vitest tests** for request method/path/body, raw-dict response handling, credential omission when unchanged, and rollback/role/provider mutations.
- [ ] **Step 2: Run the API tests** and verify failure because the module is absent.
- [ ] **Step 3: Implement one `modelConfig.ts` domain module** with typed functions for role/provider/history/drift/probe operations, using `mutationRequest` for writes.
- [ ] **Step 4: Add `/settings/models` navigation** as an admin-only item and convert the old prices page to a redirect preserving `?tab=prices`.
- [ ] **Step 5: Run the API and navigation tests** and verify both pass.

### Task 4: 实现管理端模型配置页面

**Files:**
- Create: `frontend-admin/src/app/settings/models/page.tsx`
- Create: `frontend-admin/src/components/settings/ModelRolesTab.tsx`
- Create: `frontend-admin/src/components/settings/ProvidersTab.tsx`
- Create: `frontend-admin/src/components/settings/ConfigHistoryTab.tsx`
- Create: `frontend-admin/src/components/settings/DriftTab.tsx`
- Create: `frontend-admin/src/components/settings/ConnectivityProbe.tsx`
- Create: `frontend-admin/src/components/settings/modelConfig.test.tsx`

- [ ] **Step 1: Write failing component tests** for role selection/confirmation, provider credential three-state behavior, probe grade rendering, history secret redaction, editor/admin visibility, and drift empty state.
- [ ] **Step 2: Run the component tests** and verify failure because components are absent.
- [ ] **Step 3: Implement the page shell** with `RoleGate minRole="editor"`, five tabs, loading/error/empty states, and a read-only notice for editor.
- [ ] **Step 4: Implement role binding editing** with disabled unavailable models, embedding reindex confirmation, and post-save invalidation.
- [ ] **Step 5: Implement provider editing and probe UI** with masked credential status, private-network warning, fixed probe request, and four-level result rendering.
- [ ] **Step 6: Implement history rollback and drift views**; never render secret values and disable credential rollback.
- [ ] **Step 7: Re-run component tests and `npm exec tsc -- --noEmit`** in `frontend-admin`.

### Task 5: 端到端回归和交付检查

**Files:**
- Modify: `docs/model-config-governance-progress-report-2026-09-19.md`
- Modify: `docs/model-config-admin-ui-design.md`
- Create/modify: relevant test files only if a verified regression is found.

- [ ] **Step 1: Run backend focused governance suite** with `--no-cov` and record the fresh count.
- [ ] **Step 2: Run frontend model-config tests, full admin typecheck, and admin production build.**
- [ ] **Step 3: Run read-only migration checks** (`alembic current`, `0016:0017 --sql`) without applying production changes.
- [ ] **Step 4: Run a real route inventory and authenticated smoke test** against the app/router; verify role/provider GET, role/provider writes, probe, history, drift, and masked credential behavior.
- [ ] **Step 5: Update the progress report** to reflect the actual implementation and remaining deployment-only steps.

