# Task 2 报告：P2 RBAC 与会话后端

## 结果

已完成 P2 后端契约：管理端 RBAC 路由、客服档案事务更新、RBAC 审计、角色变更后的会话撤销、登录/刷新 userInfo 权限字段，以及旧角色接口兼容转发。

## TDD 证据

### RED

先新增测试，再运行：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py::test_login_and_refresh_user_info_contains_current_rbac_fields -q --no-cov
```

输出为退出码 1，失败原因是待实现能力缺失，而非断言拼写错误：

```text
ImportError: cannot import name 'rbac' from 'backend.app.api.routes'
ModuleNotFoundError: No module named 'backend.security.session_service'
2 errors during collection
```

### GREEN

实现最小行为后，新增 RBAC/会话测试首次通过：

```text
D:/Python/python.exe -m pytest tests/security/test_session_service.py tests/api/test_rbac_api.py -q --no-cov
11 passed
```

最终 focused/auth/operator 套件：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py tests/api/test_operator_role_rbac.py -q --no-cov
39 passed, 5 warnings
```

P1 schema 套件：

```text
D:/Python/python.exe -m pytest tests/customer_service/test_cs_dispatch_schema.py -q --no-cov
13 passed
```

其余验证：

```text
D:/Python/python.exe -m py_compile app/api/routes/rbac.py security/session_service.py app/api/routes/auth_local.py app/api/router.py sql/alembic/memory/versions/0024_rbac_audit.py tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py tests/customer_service/test_cs_dispatch_schema.py
exit 0

ruff check app/api/router.py app/api/routes/auth_local.py app/api/routes/rbac.py security/session_service.py tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py
All checks passed!

029_rbac_audit.sql 在同一 PostgreSQL 事务内连续执行两次后回滚：
029 transactional double-run: OK
```

## 修改文件

- `backend/app/api/routes/rbac.py`：新增 `/sys/rbac/users`、用户 PATCH、审计分页；统一 admin 依赖；版本谓词、最后 active admin 保护、客服档案服务端绑定、同事务审计与角色变更会话撤销。
- `backend/app/api/router.py`：挂载 RBAC 路由。
- `backend/app/api/routes/auth_local.py`：复用会话撤销服务；旧 `/sys/users/{user_id}/role` 转发到同一事务实现；登录/刷新返回最新 `roles/platformRole/tenantId/csRole`。
- `backend/security/session_service.py`：新增按用户/按 session 的 DB 吊销和提交后 Redis 闸键清理服务。
- `backend/sql/alembic/memory/versions/0024_rbac_audit.py`：Alembic `0024 -> 0023`。
- `backend/sql/migrations/029_rbac_audit.sql`：幂等增加 `auth.users.version`、`auth.rbac_audits` 及索引。
- `backend/tests/api/test_rbac_api.py`：RBAC 权限、分页、非法输入、版本冲突、最后 admin、agent_id 防伪和审计测试。
- `backend/tests/security/test_session_service.py`：用户/session 吊销、Redis 降级、异常传播测试。
- `backend/tests/api/test_auth_session_family.py`：登录/刷新 userInfo RBAC 字段回归测试。
- `backend/tests/customer_service/test_cs_dispatch_schema.py`：将单 Alembic head 期望从 P1 的 `0023` 更新为新增 P2 `0024`；未修改 P1 模型或迁移内容。

用户提供的 `0014–0022` 与 `027` overlay 保持未跟踪、未暂存；未修改前端或原工作树。

## 迁移假设

- 迁移基线为 P1 `0023_cs_dispatch.py` / `028_cs_dispatch.sql`，P2 新增 `0024` / `029`。
- `auth.users`、`auth.sessions`、`auth.refresh_tokens` 已由 008/009/023 提供；部署顺序必须先完成既有基线，再执行 029。
- `auth.users` 当前没有独立租户列，登录/刷新 `tenantId` 以 `default` 为默认；客服档案租户使用统一身份入口的可信租户头，缺省为 `default`，不读取请求体租户或 agent_id。
- `auth.rbac_audits` 的 actor/target user id 使用 BIGINT；服务身份操作者可记录为 NULL，JSONB before/after 保存完整平台与客服字段。
- Alembic downgrade 保持 no-op，避免破坏已有用户、会话和审计历史；原生迁移只使用幂等加法操作。

## 风险

- Redis 是即时 access/session 闸门而非 DB 权威。Redis 不可用时，角色/会话 DB 吊销仍提交并记录 warning；旧 access token 的即时拦截依赖 Redis/gateway 恢复或自然过期。
- 当前本地测试库尚未完整应用 P1 `cs_agents.auth_user_id` 等列；登录/刷新会先探测列契约，旧库将 `csRole` 返回 null。正式启用 RBAC 前必须完成 P1 与 029 迁移。
- Windows 测试仍有既有 `PytestUnraisableExceptionWarning`（Proactor transport 清理），不影响退出码和断言结果。
- 兼容旧 role 接口不要求 version，以保持旧客户端可用；新 `/sys/rbac/users/{user_id}` 强制 version，所有真实更新仍走同一事务、审计和会话撤销路径。
