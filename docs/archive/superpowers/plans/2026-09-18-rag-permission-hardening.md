# RAG Permission Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 RAG 全部检索和管理入口的用户/部门/文档权限隔离，并以回归测试证明无越权放行。

**Architecture:** 复用现有 `RequestContext` 作为唯一身份载体，增加可信权限 claim 的透传；将文档级过滤抽成公共边界函数，由 Pipeline、Retriever 和 API 入口共同消费。未知或过滤异常按 fail-closed 处理，缓存键绑定完整授权 scope。

**Tech Stack:** FastAPI、APISIX Lua、LangChain retriever、PostgreSQL/pgvector、Redis、pytest。

**Spec:** `docs/superpowers/specs/2026-09-18-rag-permission-hardening-design.md`

## Global Constraints

- 所有回答和代码注释使用中文；不覆盖工作树已有改动。
- 新增/修改测试必须先写失败测试并实际运行确认失败，再写生产代码。
- 局部 pytest 必须使用 `--no-cov`。
- 不信任客户端提交的 user、department、permissions；只消费网关注入头或内部服务上下文。
- 缺少可信权限集合时仅允许 `general`，受限文档拒绝。

---

### Task 1: 可信权限上下文

**Files:**
- Modify: `apisix/plugins/gateway-auth.lua`
- Modify: `backend/config/auth.py`
- Modify: `backend/app/api/identity.py`
- Modify: `backend/core/request_context.py`
- Test: `backend/tests/api/test_identity.py` 或现有身份测试文件

**Interfaces:**
- 网关注入 `X-User-Permissions: p1,p2`。
- `Identity.permissions: tuple[str, ...] | None`。
- `RequestContext.permissions: tuple[str, ...] | None`。

- [ ] 写测试：JWT/可信头可解析 permissions；无头为 `None`；伪造客户端头不能直接成为可信权限。
- [ ] 运行身份测试确认失败。
- [ ] 实现网关剥离/注入、后端解析和上下文传递。
- [ ] 运行身份测试确认通过。

### Task 2: 公共文档权限过滤器

**Files:**
- Modify: `backend/rag/permissions.py`
- Modify: `backend/rag/retrieval/retrievers.py`
- Modify: `backend/rag/context.py`
- Test: `backend/tests/rag/test_quality_gate.py`
- Test: `backend/tests/rag/test_retrievers.py`

**Interfaces:**
- `filter_documents_by_permission(docs, user_permissions) -> list`，保持顺序、复制 metadata，不改变输入。
- Retriever 从当前 `RequestContext.identity.permissions` 读取并在最终返回前收口。

- [ ] 写测试：general 放行、受限拒绝、持权放行、过滤异常按空集处理、parent/adaptive/fallback 不绕过。
- [ ] 运行 RAG 定向测试确认失败。
- [ ] 实现公共过滤器和 Retriever 集成。
- [ ] 运行定向测试确认通过。

### Task 3: Pipeline/远程/原始搜索统一授权

**Files:**
- Modify: `backend/rag/pipeline.py`
- Modify: `backend/rag/client.py`
- Modify: `backend/services/rag_server.py`
- Modify: `backend/app/api/routes/rag_search.py`
- Modify: `backend/app/api/routes/rag.py`
- Modify: `backend/tools/rag.py`
- Test: `backend/tests/test_rag_remote_mode.py`
- Test: `backend/tests/rag/test_rag_identity_composition.py`
- Test: new focused RAG API tests if existing coverage is absent

**Interfaces:**
- `ask(..., permissions=None)` 和 `retrieve_knowledge(..., subject_type="", department="", permissions=None)`。
- remote `/ask`、`/retrieve` 传输同一授权字段。
- `/rag/search` 使用请求身份构造上下文后再返回结果。

- [ ] 写测试：轻量检索、remote retrieve、`/rag/search` 均剔除受限文档；显式越界 KB 仍为空。
- [ ] 运行测试确认失败。
- [ ] 实现统一授权上下文准备和所有出口收口。
- [ ] 运行测试确认通过。

### Task 4: 缓存和管理面守卫

**Files:**
- Modify: `backend/rag/answer_cache.py`
- Modify: `backend/rag/pipeline.py`
- Modify: `backend/app/api/routes/rag_documents.py`
- Modify: `backend/app/api/routes/rag_upload.py`
- Modify: `backend/app/api/routes/rag_search.py`
- Test: cache and API authorization regression tests

- [ ] 写测试：不同 permissions 不共享答案缓存；非 JWT 用户不能访问管理面；admin/editor 角色矩阵符合现有守卫约定。
- [ ] 运行测试确认失败。
- [ ] 实现权限 scope hash 和路由依赖。
- [ ] 运行 API/cache 测试确认通过。

### Task 5: 回归验证

- [ ] 运行 `python -m py_compile` 覆盖所有改动 Python 文件。
- [ ] 运行 `cd backend && D:/Python/python.exe -m pytest tests/rag tests/test_rag_remote_mode.py tests/api/test_identity.py -q --no-cov`。
- [ ] 运行项目规定的 registry/layer/ADR 守护测试。
- [ ] 检查 `git diff --stat` 和逐文件 diff，确认不触碰已有无关改动。
