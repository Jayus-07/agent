# Organization / RBAC / Data Scope Production Closure — 实施方案（含真实链路审计）

> 日期：2026-09-23 ｜ 状态：实施中 ｜ 上游需求：用户下发《授权生产收口》九十八节任务书（已归档于会话，本文件为落地版）
> 目标：企业身份、部门、角色、数据范围与 RAG 权限链生产收口，最终输出 `AUTHORIZATION_PRODUCTION_READY=true/false`。
> 铁律：**认证只回答"你是谁"，授权统一回答"你能做什么、能看什么"；客户端只能提出请求，不能声明自己的身份和权限。**

---

## 一、审计结论（2026-09-23 实测，先读后写）

### 1.1 现有链路（比任务书假设的更完整）

| 环节 | 现状 | 位置 |
|---|---|---|
| 登录 | ✅ py 自建：验密 + 读 DB + 签 JWT | `backend/app/api/routes/auth_local.py` |
| JWT claims | ✅ 已带 `userId/username/dept/roles/tenant_id/sid/jti/type` | `backend/security/local_jwt.py::issue_access_token` |
| refresh | ✅ **已重读 DB**（JOIN auth.users 取最新 dept/role 后签发，三处签发点统一 `_jwt_roles`） | `auth_local.py::refresh` |
| APISIX 验签+注入 | ✅ 剥九伪造头 → 黑名单 → 验签 → issuer/type/会话闸 → 注入 X-Auth-Type/X-User-Id/X-User-Name/X-User-Dept/X-User-Roles/X-User-Permissions/X-Tenant-Id | `apisix/plugins/gateway-auth.lua` |
| backend 身份解析 | ✅ 已有单一入口 `resolve_identity`（legacy/header/strict 三模式；header/strict 下 body 身份字段一律无视） | `backend/app/api/identity.py` |
| KB 授权核心 | ✅ 已有确定性单一来源 `authorized_kbs(subject_type, department)`（检索层 `_scope_kb_filter` 收敛，显式 kb 选择同样受限） | `backend/config/knowledge_base.py` + `backend/rag/retrieval/retrievers.py::_load_request_context` |
| RBAC 管理面 | ✅ 用户列表/详情/创建/角色状态更新/重置密码/强制下线/审计（`auth.rbac_audits`，乐观锁 version + advisory lock）；角色变更/禁用自动吊销会话 | `backend/app/api/routes/rbac.py` |
| 运行时开关 | `IDENTITY_SOURCE=header`、`GATEWAY_AUTH_MODE=enforce`、`GATEWAY_SESSION_CHECK=enforce` | 根 `.env` |

### 1.2 真实缺口（本阶段要修的）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | **subject_type 推导散落 3 处、两套口径**：routes 内联 `authenticated→employee else customer`；`tools/rag.py` 内联 `dept→employee / auth→employee空部门 / else customer`（含 failsafe 开关） | `routes/rag.py:37`、`routes/rag_search.py:55,74,115`、`tools/rag.py:28-48` |
| G2 | **无统一 Principal / AuthorizationContext**；`allowed_kb_ids` 无单一计算点（`authorized_kbs` 存在但没人以"授权上下文"形态暴露） | 全仓无 principal 模块 |
| G3 | **dept 无法由管理员维护**：`update_user_in_transaction` 只更新 role/status，PATCH 契约无 dept；前端 admin 编辑弹窗无部门字段 | `rbac.py` `_validate_body` |
| G4 | **dept 无主数据校验**：创建用户 dept 自由文本，可写 "finance123xxx"；无 departments 表；前端部门列表硬编码 | `rbac.py::create_user`、`frontend-admin/src/lib/department.ts` |
| G5 | **登录/刷新响应 userInfo 不含 dept** → 前端无处展示"我的部门" | `auth_local.py::_user_info` |
| G6 | **用户端聊天页部门选择器是误导 UI**：body `department` 后端根本不消费（chat 用 `ident.department` 即 JWT dept claim），header 模式下选择器对授权零作用 | `frontend/src/components/agent/ComposerToolbar.tsx`、`chat.py:169` |
| G7 | 权限点无稳定 code 体系（角色矩阵散在 prompts/_check_permission、gateway ROLE_RANK、deps._ROLE_RANK） | 三处各自维护 |
| G8 | 缺 header spoof / body spoof / dept 变更 E2E 的真实回归测试 | tests 无对应文件 |

### 1.3 明确不做（沿用任务书红线）

不重写网关；不建第二套认证；不引入 Keycloak/OAuth/ABAC DSL；不做组织树；不做 SQL RLS/CS 权限实现（只留 `data_scope` 表达位）；不改 RAG 检索算法与授权语义（`c4b865b` 修正语义**迁移不删除**）；不推翻 JWT 结构（claims 已齐备）。

---

## 二、实施方案

### A. 统一 Principal + AuthorizationContext（Commit A `feat(auth)`）

新增 `backend/security/principal.py`：

```python
class Principal:            # frozen dataclass
    user_id / user_name / tenant_id / department: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...] | None
    subject_type: Literal["employee", "customer", "service", "anonymous"]
    authenticated: bool
    auth_type / source: str
```

**subject_type 唯一推导规则**（集中一处， department 只是组织属性、不参与判定）：

| 通道 | 判定 | subject_type |
|---|---|---|
| HTTP 网关通道 | `identity.authenticated`（header/strict 下仅认网关注入头） | `employee` |
| HTTP 网关通道 | 未认证（guest/anonymous/api-key 无用户） | `customer`（**对客 fail-safe，宁严勿漏**；authenticated=False 标记匿名。不采用"未认证→anonymous 弱语义"，因为 RAG 现行已验证授权语义 = customer fail-safe，见任务书 §31"以当前已验证规则为准"） |
| Tool/contextvars 通道（图路径） | `dept 非空` → employee+dept；`已登录` → employee+空部门；否则 `RAG_TOOL_FAILSAFE_CUSTOMER=true` → customer，`false` → 未声明（旧行为） | 同上（**c4b865b 语义原样迁移**） |
| 服务凭据（X-Internal-Token） | 预留 `service`（本阶段仅类型定义，不改授权行为） | `service` |

新增 `backend/security/authorization.py`：

```python
class AuthorizationContext:   # frozen dataclass，每 HTTP/graph run 只 build 一次
    principal: Principal
    allowed_kb_ids: frozenset[str] | None   # None = 未声明主体（授权未启用，旧行为）
    permission_codes: frozenset[str]
    data_scope: str | None                  # all | department | self（未来 SQL 用，只表达不实现）
    def has_permission(code) / require_permission(code)
def build_authorization_context(principal) -> AuthorizationContext
```

- `allowed_kb_ids` **单一计算点** = 复用 `knowledge_base.authorized_kbs`（已验证矩阵，零重复实现）。
- permission_codes 由 role 推导（JWT 只带 roles，不带权限点，体积小且权限可更新）：
  `viewer→{rag.read}`；`editor→+{rag.upload, rag.review}`；`admin→+{admin.users.read, admin.users.write, rag.admin}`；cs 角色不映射平台权限点。
- request-scoped 缓存：FastAPI 依赖 `get_principal` / `get_authorization_context`（`backend/security/dependencies.py`），Depends 每请求天然缓存一次；毫秒级（纯内存计算，无 DB 查询）。

### B. Department 数据闭环（Commit B `feat(auth)`）

1. Migration `041_auth_departments.sql`（幂等可重放，存量用户 dept='' 不受影响）：
   - `auth.departments(id, tenant_id, code, name, status, created_at, updated_at, UNIQUE(tenant_id, code))`
   - seed：`config/knowledge_base.py::DEPARTMENTS` 九部门 × tenant `default`（ON CONFLICT DO NOTHING）
   - **权威口径**：DB 为部门主数据权威；KB `owner_depts` 矩阵仍以 code 关联（本阶段不迁移 KB 定义，避免破坏已验证授权语义）。
2. `rbac.py`：
   - `PATCH /sys/rbac/users/{id}` 支持 `dept`：空串=清空；非空必须命中本租户 active 部门，否则 400 `INVALID_DEPARTMENT`；before/after 进既有 `auth.rbac_audits`（actor/target/old/new 全记录）；**dept 变更与 role 变更同语义吊销该用户全部会话**（rbac_changed——平台已有会话闸，改权即失效，不等 30min TTL）。
   - 新增 `GET /sys/rbac/departments`：本租户 active 部门列表（管理端下拉唯一数据源，禁前端写死）。
   - `create_user` 的 dept 同口径校验。
3. `_user_info`（login/refresh 响应）增加 `dept` —— 用户端可展示"我的部门"。

### C. RAG 接入统一授权（Commit D `refactor(rag)`）

- `routes/rag.py` / `routes/rag_search.py` / `tools/rag.py`：删除各自内联 subject_type 推导，统一改调 `resolve_principal` / `resolve_tool_principal`；行为与 c4b865b 后完全一致（守卫测试 `test_rag_subject_resolution.py` 不改断言必须全绿）。
- kb_id/department 只能收窄：检索层 `_scope_kb_filter` 已保证（审计确认），本阶段补测试锁定。
- Chat body 的 `department` 字段：header 模式本就不消费（`ChatRequest.department` 标注 deprecated）；RAG 路由不接收 body 身份。

### D. 网关与直连策略（Commit C `fix(gateway)`）

- lua 剥头/注入已实现（审计确认），本阶段补**真实 E2E**：`backend/scripts/verify_identity_headers.py` 走 APISIX :9080 验证 ①JWT 正常注入 ②伪造 X-User-Dept/X-User-Roles/X-User-Id 被剥离 ③无 JWT 401。app:8000 直连信任边界 = 网络边界（compose 已收口 127.0.0.1，header 模式前提），报告明确。

### E. 前端（Commit F）

- **frontend-admin**：用户编辑弹窗加部门下拉（数据源 `GET /sys/rbac/departments`，可清空）；保存后提示"授权变更已生效，该用户需重新登录"；用户详情/列表展示部门。
- **frontend（用户端）**：聊天页部门选择器改为**只读徽标**"当前部门：X"（来自登录 userInfo.dept），移除误导性切换；`useSSE` 不再发送 body department。

### F. 验收（Commit E `test(auth)`）

- 单测：principal 推导矩阵（employee 有/无部门、guest、service 预留）、KB 授权矩阵、permission map、dept 更新校验+审计+会话吊销、departments API、refresh 重读 DB、kb 收窄 body spoof。
- 守卫回归：`test_registry_consistency / test_layer_consistency / test_adr0001` + 既有 `test_identity / test_rbac_api / test_rag_subject_resolution / test_production_auth`（全部 `--no-cov`）。
- 真入口 E2E：APISIX :9080 spoof 脚本（网关在跑时执行）。

## 三、完成标准（对照任务书九十六节 20 条）

Principal/AuthorizationContext 单一权威 ✅ → RAG 三入口不再自行推导 ✅ → employee/customer 不看 department ✅ → admin 可维护 dept/roles + 校验 + 审计 ✅ → JWT 带 claims + refresh 重读 DB ✅ → APISIX 剥伪造头（E2E 证明）✅ → body 无法提权（kb 只收窄）✅ → 无部门员工最小权限（只 all/general 库）✅ → 部门隔离矩阵真实通过 ✅ → anonymous fail-safe 不回归 ✅ → Admin API 后端 403 ✅ → audit 全记录 ✅ → Chat/RAG/Citation 无回归 ✅ → :9080 真入口测试 ✅ → 权限处理毫秒级 ✅ → 守卫测试零新增失败 ✅ ⇒ `AUTHORIZATION_PRODUCTION_READY=true`
