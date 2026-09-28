# 三端 Super Admin RBAC 最小改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不重构现有认证授权体系的前提下，为三个端增加正确访问 Gate，并以 `super_admin` 补齐平台角色管理边界与会话立即失效闭环。

**Architecture:** 平台角色仍由 `auth.users.role`、JWT `roles`、FastAPI 统一角色解析和 APISIX rank 共同表达；新增 `super_admin` 仅扩展该静态枚举。RBAC 路由使用一个 transition helper 决定操作者可否修改目标角色/状态，SessionService 保持既有事务内吊销、提交后清 Redis 的职责。三个前端只在既有 AuthGate 增加 UX 判定，后端与网关独立维持安全边界。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy、PostgreSQL、Redis、APISIX Lua、Next.js 14、React、TypeScript、Vitest、pytest。

**Spec:** `docs/superpowers/specs/2026-09-28-rbac-super-admin-design.md`

## Global Constraints

- 平台角色仅为 `viewer`、`editor`、`admin`、`super_admin`，客服角色独立为 `null | agent | supervisor`。
- 不引入 Casbin、OPA、动态权限树、权限表或角色权限表；不重写 JWT、Session、Refresh 或 APISIX 鉴权。
- 服务凭据与 `X-Internal-Token` 始终仅映射 `admin`，绝不能映射为 `super_admin`。
- 公开注册固定创建 `viewer`，不接受客户端传入的平台或客服角色。
- 普通管理 HTTP API 永远不能创建、授予、撤销、降级、禁用或其他修改 `super_admin`。
- 授权属性变化在数据库提交后立即删除目标用户 Redis access-session keys；`accepting`、`maxConversations`、`displayName` 不得触发吊销。
- 所有 Python 注释使用中文；局部 pytest 均显式追加 `--no-cov`。
- 不改动现有用户的角色值；迁移必须幂等，并在 `scripts/init_db.py` 登记。

## Review Focus

- `super_admin` 是 11 字符，migration 必须先把 `auth.users.role` 从 `VARCHAR(10)` 扩至至少 `VARCHAR(11)`，再更新 CHECK。
- 旧 `/sys/users/{id}/role` 路径必须经过与新 RBAC API 完全相同的 transition helper，不能成为越权旁路。
- 有 active `super_admin` 时可降级最后一名普通 admin；无高权限账户时仍必须阻止最后一名高权限账户被降级或禁用。
- refresh 后必须覆盖前端缓存的 `userInfo`；旧 access/refresh 在授权变更后应同时失效，而不是只等待 access TTL。
- 客服端的 `agent/supervisor` 判断与平台 rank 必须解耦；无 `csRole` 的 admin/super_admin 不能用客服工作台。

---

### Task 1: 角色枚举、统一守卫与网关等级

**Files:**
- Modify: `backend/app/api/deps.py:251-340`
- Modify: `backend/app/api/routes/auth_local.py:647-702`
- Modify: `apisix/plugins/gateway-auth.lua:251-340`
- Test: `backend/tests/api/test_rbac_api.py`
- Test: `backend/tests/api/test_auth_local.py`（若不存在则创建）

**Interfaces:**
- Consumes: `OperatorIdentity(role: str, actor: str)` 与 `resolve_operator_role(request)`。
- Produces: `_KNOWN_ROLES`、`_ROLE_RANK`、`is_platform_admin(role: str) -> bool`；`require_admin_user()` 对 `admin`/`super_admin` 放行；APISIX `ROLE_RANK.super_admin=3`。

- [ ] **Step 1: 写失败测试，固定统一 admin guard 与服务凭据语义**

```python
@pytest.mark.anyio
async def test_require_admin_user_accepts_super_admin(monkeypatch):
    monkeypatch.setattr(deps, "require_user_actor", _super_admin_actor)
    identity = await deps.require_admin_user(Request(scope={"type": "http"}))
    assert identity.role == "super_admin"

def test_internal_token_never_maps_to_super_admin():
    assert deps.OperatorIdentity(role="admin", actor="service:internal-token").role == "admin"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend; D:/Python/python.exe -m pytest tests/api/test_auth_local.py -q --no-cov`

Expected: `super_admin` 尚未被识别或 admin guard 返回 403。

- [ ] **Step 3: 实现最小角色扩展**

```python
_KNOWN_ROLES = ("viewer", "editor", "admin", "super_admin")
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "super_admin": 3}

def is_platform_admin(role: str) -> bool:
    return role in {"admin", "super_admin"}
```

令 `require_admin_user` 与所有直接复用平台 admin 语义的 guard 使用
`is_platform_admin`；保留 `resolve_operator_role` 的服务身份返回 `admin`。
把 APISIX rank 改为 `{ viewer = 0, editor = 1, admin = 2, super_admin = 3 }`，
不将 `agent` 或 `supervisor` 放入该表。

- [ ] **Step 4: 让旧角色接口复用统一语义**

将 `auth_local.py` 的 `_ALLOWED_ROLES` 扩为四角色，旧 PATCH 不再以
`operator.role != "admin"` 自行判断；它仅验证请求角色格式，最终授权交给
Task 2 的 transition helper。

- [ ] **Step 5: 运行后端相关测试确认通过**

Run: `cd backend; D:/Python/python.exe -m pytest tests/api/test_auth_local.py tests/api/test_rbac_api.py -q --no-cov`

Expected: 退出码 0；服务凭据仍为 admin，super_admin 满足统一 admin guard。

- [ ] **Step 6: 检查网关语法并提交**

Run: `git diff --check -- backend/app/api/deps.py backend/app/api/routes/auth_local.py apisix/plugins/gateway-auth.lua backend/tests/api/test_auth_local.py backend/tests/api/test_rbac_api.py`

```powershell
git add -A -- backend/app/api/deps.py backend/app/api/routes/auth_local.py apisix/plugins/gateway-auth.lua backend/tests/api/test_auth_local.py backend/tests/api/test_rbac_api.py
git commit -m "feat(auth): recognize super admin role" -- backend/app/api/deps.py backend/app/api/routes/auth_local.py apisix/plugins/gateway-auth.lua backend/tests/api/test_auth_local.py backend/tests/api/test_rbac_api.py
```

### Task 2: 集中角色转换规则与会话吊销边界

**Files:**
- Modify: `backend/app/api/routes/rbac.py:25-478`
- Modify: `backend/app/api/routes/rbac.py:678-776`
- Test: `backend/tests/api/test_rbac_api.py`
- Test: `backend/tests/security/test_session_service.py`

**Interfaces:**
- Consumes: `OperatorIdentity`、`is_platform_admin`、`SessionService.revoke_user_sessions(db, user_id, reason)`。
- Produces: `_validate_role_transition(operator_role: str, current_role: str | None, new_role: str, new_status: int, *, creating: bool = False) -> None`，在失败时抛出 403 `HTTPException`。

- [ ] **Step 1: 写失败的权限矩阵测试**

```python
@pytest.mark.parametrize("body", [
    {"version": 0, "platformRole": "admin"},
    {"version": 0, "status": 0},
])
def test_admin_cannot_promote_or_modify_admin(monkeypatch, body):
    session = _FakeRbacSession(target_role="admin")
    response = _client(monkeypatch, session, operator_role="admin").patch(
        "/api/sys/rbac/users/2", json=body
    )
    assert response.status_code == 403

def test_super_admin_can_demote_admin(monkeypatch):
    session = _FakeRbacSession(target_role="admin")
    response = _client(monkeypatch, session, operator_role="super_admin").patch(
        "/api/sys/rbac/users/2", json={"version": 0, "platformRole": "viewer"}
    )
    assert response.status_code == 200
```

同时覆盖：admin 的 viewer/editor 双向切换、agent/supervisor/null；admin 创建 admin
为 403；super_admin 创建 admin 为 200；任意操作者创建或写入 `super_admin` 为 403；
任意 HTTP 更新既有 super_admin 为 403。

- [ ] **Step 2: 运行权限矩阵测试确认失败**

Run: `cd backend; D:/Python/python.exe -m pytest tests/api/test_rbac_api.py -q --no-cov`

Expected: 当前普通 admin 的 admin 创建或 admin 目标修改用例会错误成功。

- [ ] **Step 3: 实现一个 transition helper 并接入 PATCH 与 POST**

```python
def _validate_role_transition(
    operator_role: str,
    current_role: str | None,
    new_role: str,
    new_status: int,
    *,
    creating: bool = False,
) -> None:
    if current_role == "super_admin" or new_role == "super_admin":
        raise _http_error("super_admin 只能由运维 bootstrap 管理", 403)
    if operator_role == "admin" and (
        current_role == "admin" or new_role == "admin"
    ):
        raise _http_error("仅 super_admin 可管理 admin", 403)
    if operator_role not in {"admin", "super_admin"}:
        raise _http_error("仅管理员可管理 RBAC", 403)
```

在读取并锁定目标用户、计算 `new_role` 和 `new_status` 后调用 helper；创建接口以
`current_role=None, creating=True` 调用。保留租户、版本、部门、客服档案、审计与
临时密码逻辑，不复制授权判断到各 endpoint。

- [ ] **Step 4: 扩展最后高权限账户保护**

将 active 管理员锁查询改为 `role IN ('admin', 'super_admin')`，并在目标从该集合
离开或被禁用时阻止最后一位活动高权限账户被移除。该保护在 helper 之后执行，
不允许它绕过 super_admin 的 HTTP 保护规则。

- [ ] **Step 5: 写会话撤销语义的失败测试**

```python
@pytest.mark.parametrize("body", [
    {"version": 0, "accepting": False},
    {"version": 0, "maxConversations": 20},
])
def test_operational_cs_changes_do_not_revoke_sessions(monkeypatch, body):
    session = _FakeRbacSession()
    response = _client(monkeypatch, session).patch("/api/sys/rbac/users/2", json=body)
    assert response.status_code == 200
    assert response.json()["revokedSessionCount"] == 0
    assert session.sessions[0]["revoked_at"] is None
```

保留并断言 platformRole、dept、csRole、客服 `enabled` 与用户 disabled 会撤销
`auth.sessions`、所有 refresh token 及 Redis access-session key。

- [ ] **Step 6: 收口授权字段判断并运行测试**

将 `cs_authorization_changed` 的字段集合从 `("csRole", "enabled", "accepting")`
改为 `("csRole", "enabled")`；不要改 `SessionService` 的事务/Redis 实现。

Run: `cd backend; D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/security/test_session_service.py -q --no-cov`

Expected: 退出码 0；原“accepting 会撤销”的测试已替换为“不撤销”断言。

- [ ] **Step 7: 提交 RBAC 收口改动**

```powershell
git add -A -- backend/app/api/routes/rbac.py backend/tests/api/test_rbac_api.py backend/tests/security/test_session_service.py
git commit -m "feat(rbac): enforce super admin transitions" -- backend/app/api/routes/rbac.py backend/tests/api/test_rbac_api.py backend/tests/security/test_session_service.py
```

### Task 3: 数据库迁移与受控 super_admin bootstrap

**Files:**
- Create: `backend/sql/migrations/054_auth_super_admin.sql`
- Modify: `scripts/init_db.py:80-180`
- Create: `backend/scripts/bootstrap_super_admin.py`
- Test: `backend/tests/scripts/test_bootstrap_super_admin.py`
- Test: `backend/tests/sql/test_auth_super_admin_migration.py`

**Interfaces:**
- Consumes: `auth.users`、`auth.rbac_audits`、`SessionService` 与 `backend.memory.database.get_session()`。
- Produces: 可重复执行的 migration；`python -m backend.scripts.bootstrap_super_admin --tenant TENANT (--username NAME | --user-id ID)`。

- [ ] **Step 1: 写 migration 与 CLI 的失败测试**

```python
def test_super_admin_migration_widens_role_and_checks_all_four_roles():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "VARCHAR(11)" in sql
    assert "'super_admin'" in sql
    assert "DROP CONSTRAINT IF EXISTS ck_users_role" in sql

def test_bootstrap_requires_tenant_and_exactly_one_selector():
    assert bootstrap.main(["--tenant", "t"]) == 2
    assert bootstrap.main(["--tenant", "t", "--username", "a", "--user-id", "1"]) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend; D:/Python/python.exe -m pytest tests/sql/test_auth_super_admin_migration.py tests/scripts/test_bootstrap_super_admin.py -q --no-cov`

Expected: migration 与 bootstrap 模块不存在。

- [ ] **Step 3: 添加幂等 migration 并登记**

```sql
ALTER TABLE auth.users
    ALTER COLUMN role TYPE VARCHAR(11);

ALTER TABLE auth.users
    DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE auth.users
    ADD CONSTRAINT ck_users_role
    CHECK (role IN ('viewer', 'editor', 'admin', 'super_admin'));
```

在 `MIGRATION_TARGETS` 登记 `054_auth_super_admin.sql: memory`。migration 不更新
任何现有用户，不新增角色表。

- [ ] **Step 4: 实现只提升既有用户的 bootstrap**

脚本使用 argparse 要求 `--tenant` 与互斥 selector；在一个异步数据库事务中按
tenant 锁定用户，用户不存在即抛出非零错误，更新角色为 `super_admin`，写入
`auth.rbac_audits(action='user.bootstrap_super_admin')`，调用
`SessionService.revoke_user_sessions()`，commit 后调用 `clear_redis_for_sessions()`。
脚本不接受密码、不创建用户，stdout 仅写 user id、tenant、审计动作和撤销数量。

- [ ] **Step 5: 运行 migration/CLI 测试与语法检查**

Run: `cd backend; D:/Python/python.exe -m py_compile scripts/bootstrap_super_admin.py; D:/Python/python.exe -m pytest tests/sql/test_auth_super_admin_migration.py tests/scripts/test_bootstrap_super_admin.py -q --no-cov`

Expected: 退出码 0；测试覆盖不存在用户失败、审计、session/refresh 吊销与 Redis 清理调用。

- [ ] **Step 6: 提交 migration 与 bootstrap**

```powershell
git add -A -- backend/sql/migrations/054_auth_super_admin.sql scripts/init_db.py backend/scripts/bootstrap_super_admin.py backend/tests/sql/test_auth_super_admin_migration.py backend/tests/scripts/test_bootstrap_super_admin.py
git commit -m "feat(auth): add super admin bootstrap" -- backend/sql/migrations/054_auth_super_admin.sql scripts/init_db.py backend/scripts/bootstrap_super_admin.py backend/tests/sql/test_auth_super_admin_migration.py backend/tests/scripts/test_bootstrap_super_admin.py
```

### Task 4: 三端前端 Gate 与角色类型

**Files:**
- Modify: `frontend/src/components/AuthGate.tsx`
- Modify: `frontend-admin/src/components/AuthGate.tsx`
- Modify: `frontend-admin/src/app/layout.tsx`
- Modify: `frontend-admin/src/lib/auth.ts`
- Modify: `frontend-admin/src/api/rbac.ts`
- Modify: `frontend-cs/src/components/AuthGate.tsx`
- Modify: `frontend-cs/src/app/layout.tsx`
- Modify: `frontend-cs/src/lib/auth.ts`
- Modify: `frontend-cs/src/api/rbac.ts`
- Test: `frontend/src/components/AuthGate.test.tsx`
- Test: `frontend-admin/src/components/AuthGate.test.tsx`
- Test: `frontend-cs/src/components/AuthGate.test.tsx`

**Interfaces:**
- Consumes: `getAccessToken()`、`tryRefreshOnce()`、`getCachedUser()`、`atLeast("admin")`、`getCsRole()`。
- Produces: AuthGate 在认证成功后返回 children 或带 `role="alert"` 的无权限页面；`PlatformRole` 包含 `super_admin`。

- [ ] **Step 1: 写三个 Gate 的失败组件测试**

```tsx
it("super_admin 可以渲染管理端内容", () => {
  sessionStorage.setItem("agent.access_token", "token")
  sessionStorage.setItem("agent.user_info", JSON.stringify({ roles: ["super_admin"] }))
  renderGate(<AuthGate><span>admin-page</span></AuthGate>)
  expect(screen.getByText("admin-page")).toBeTruthy()
})

it("没有 csRole 的 admin 不渲染客服工作台", () => {
  sessionStorage.setItem("agent.access_token", "token")
  sessionStorage.setItem("agent.user_info", JSON.stringify({ roles: ["admin"], platformRole: "admin" }))
  renderGate(<AuthGate><span>cs-page</span></AuthGate>)
  expect(screen.queryByText("cs-page")).toBeNull()
  expect(screen.getByRole("alert").textContent).toContain("无客服工作台访问权限")
})
```

同时保留用户端“持有 token 的 viewer 可访问”测试，覆盖 refresh 成功后更新 userInfo
再判定 Gate 的路径。

- [ ] **Step 2: 运行三个失败测试**

Run: `cd frontend-admin; npm test -- --run src/components/AuthGate.test.tsx; cd ../frontend-cs; npm test -- --run src/components/AuthGate.test.tsx; cd ../frontend; npm test -- --run src/components/AuthGate.test.tsx`

Expected: 管理端与客服端当前只检查 token，越权测试错误渲染 children。

- [ ] **Step 3: 实现最小 UX Gate**

用户端继续只做认证检查。管理端 AuthGate 在 token 或 refresh 成功且写入 userInfo 后，
用 `atLeast("admin")` 判断；客服端用 `getCsRole()` 判断 `agent`/`supervisor`。拒绝时
渲染中文无权限提示而非 children。调整管理端/客服端 layout，使侧栏和业务内容均在
AuthGate 成功分支内，不让未授权用户看见工作台壳。

在管理端与客服端 `ROLE_RANK` 加入 `super_admin: 3`，两处 `PlatformRole` 联合类型
加入 `super_admin`。访问控制页保留创建/编辑 select 的三项 `viewer/editor/admin`，
只把 super_admin 用于显示与类型兼容，绝不作为 UI 可选项。

- [ ] **Step 4: 收紧客服网关角色白名单**

将 `/api/cs` 的 `any_of` 从 `admin/supervisor/agent` 改为仅
`supervisor/agent`。保留用户消费者端 exempt patterns，不改其普通用户客服抽屉行为。

- [ ] **Step 5: 运行前端单测、TypeScript 检查与 API 类型测试**

Run: `cd frontend-admin; npm test -- --run src/components/AuthGate.test.tsx src/lib/auth.test.ts src/api/rbac.test.ts; npx tsc --noEmit`

Run: `cd frontend-cs; npm test -- --run src/components/AuthGate.test.tsx src/lib/auth.test.ts src/api/rbac.test.ts; npx tsc --noEmit`

Run: `cd frontend; npm test -- --run src/components/AuthGate.test.tsx; npx tsc --noEmit`

Expected: 三个命令均退出码 0；未经要求的前端文件不被修改。

- [ ] **Step 6: 提交前端 Gate**

```powershell
git add -A -- frontend/src/components/AuthGate.tsx frontend/src/components/AuthGate.test.tsx frontend-admin/src/components/AuthGate.tsx frontend-admin/src/components/AuthGate.test.tsx frontend-admin/src/app/layout.tsx frontend-admin/src/lib/auth.ts frontend-admin/src/api/rbac.ts frontend-admin/src/lib/auth.test.ts frontend-admin/src/api/rbac.test.ts frontend-cs/src/components/AuthGate.tsx frontend-cs/src/components/AuthGate.test.tsx frontend-cs/src/app/layout.tsx frontend-cs/src/lib/auth.ts frontend-cs/src/api/rbac.ts frontend-cs/src/lib/auth.test.ts frontend-cs/src/api/rbac.test.ts apisix/plugins/gateway-auth.lua
git commit -m "feat(portals): gate access by platform and cs roles" -- frontend/src/components/AuthGate.tsx frontend/src/components/AuthGate.test.tsx frontend-admin/src/components/AuthGate.tsx frontend-admin/src/components/AuthGate.test.tsx frontend-admin/src/app/layout.tsx frontend-admin/src/lib/auth.ts frontend-admin/src/api/rbac.ts frontend-admin/src/lib/auth.test.ts frontend-admin/src/api/rbac.test.ts frontend-cs/src/components/AuthGate.tsx frontend-cs/src/components/AuthGate.test.tsx frontend-cs/src/app/layout.tsx frontend-cs/src/lib/auth.ts frontend-cs/src/api/rbac.ts frontend-cs/src/lib/auth.test.ts frontend-cs/src/api/rbac.test.ts apisix/plugins/gateway-auth.lua
```

### Task 5: 集成验证与上线验收记录

**Files:**
- Modify: `backend/tests/api/test_rbac_api.py`
- Modify: `backend/tests/api/test_auth_local.py`
- Modify: `frontend-admin/src/components/AuthGate.test.tsx`
- Modify: `frontend-cs/src/components/AuthGate.test.tsx`
- Modify: `docs/superpowers/specs/2026-09-28-rbac-super-admin-design.md`（仅补充实际命令与结果）

**Interfaces:**
- Consumes: Tasks 1–4 的最终 API、JWT claim、migration、bootstrap 和 Gate。
- Produces: 可复现的测试输出与真实 HTTP/浏览器验收记录；若 Docker 或浏览器不可用，明确记录未验证并将最终 PASS 置为 false。

- [ ] **Step 1: 运行后端契约与项目规定的一致性测试**

Run: `cd backend; D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/api/test_auth_local.py tests/security/test_session_service.py tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov`

Expected: 退出码 0；覆盖注册固定 viewer、admin/super_admin 转换矩阵、JWT login/refresh claims、session/refresh/Redis 撤销与 accepting/maxConversations 不撤销。

- [ ] **Step 2: 运行迁移健康检查（仅运行环境可用时）**

Run: `D:/Python/python.exe scripts/init_db.py --port 5433 --check`

Expected: `054_auth_super_admin.sql` 被登记为 memory migration；不写入数据库。

- [ ] **Step 3: 执行真实 HTTP 与浏览器最小验收（仅 Docker Desktop 与三端服务可用时）**

先只读执行 `docker ps -a`、`netstat -ano | findstr :9080`、
`netstat -ano | findstr :3100`、`netstat -ano | findstr :3200`、
`netstat -ano | findstr :3300`。随后按受控测试账户验证：

```text
viewer                  → 用户端允许、管理端拒绝、客服端拒绝
viewer + agent          → 用户端允许、管理端拒绝、客服端允许
viewer + supervisor     → 用户端允许、管理端拒绝、客服端允许
admin                   → 用户端允许、管理端允许、客服端拒绝
super_admin             → 用户端允许、管理端允许、客服端拒绝
```

使用 super_admin 将 viewer 提升 admin，验证 200；使用 admin 创建 admin、降级 admin、
操作 super_admin，验证 403。撤销 agent 后确认旧 access 的下一请求 401、refresh 失败；
仅改 accepting 后确认请求仍保持 200。

- [ ] **Step 4: 输出证据并提交验证记录**

将每条命令、退出码、HTTP 状态、浏览器矩阵实测结果写回设计文档；未能执行的项目写明
环境原因，最终报告相应 PASS 必须为 false。

```powershell
git add -A -- backend/tests/api/test_rbac_api.py backend/tests/api/test_auth_local.py frontend-admin/src/components/AuthGate.test.tsx frontend-cs/src/components/AuthGate.test.tsx docs/superpowers/specs/2026-09-28-rbac-super-admin-design.md
git commit -m "test(rbac): verify super admin production patch" -- backend/tests/api/test_rbac_api.py backend/tests/api/test_auth_local.py frontend-admin/src/components/AuthGate.test.tsx frontend-cs/src/components/AuthGate.test.tsx docs/superpowers/specs/2026-09-28-rbac-super-admin-design.md
```
