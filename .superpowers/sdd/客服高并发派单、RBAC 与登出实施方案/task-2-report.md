# Task 2 修复报告：P2 RBAC 与会话后端

## 状态

已针对审查意见完成 P2 修复，未修改 P3 文件、前端、原工作树、P1 模型/迁移或用户的 `0014–0022/027` overlay。修复 commit message：`fix: close P2 RBAC review gaps`。

本轮复审补证仍保持 P2 范围，仅增加真实行为测试与本报告；未修改生产代码或 P3 文件。

## 本轮修复内容

- `rbac.py` 创建客服档案时写入稳定的 `display_name`：优先使用服务端 `real_name`，其次 `username`，最后回退为 `user-{id}`；新增真实非空约束回归测试，并将返回对象带上显示名。
- `csRole`、客服档案 `enabled`、`accepting` 变化与平台角色/禁用变化一样，在同一个数据库事务中吊销目标用户的 session 与全部 refresh token family；事务提交后清理 Redis access/session 闸键。行为测试断言 DB session、refresh token、Redis 闸键和旧 refresh 请求均失效，而非只断言调用次数。
- 租户从可信 identity/header 契约读取，缺失时拒绝，不再回退 `default`。用户列表、更新、审计查询均带租户条件；登录/refresh 按租户查询并返回真实 `tenantId/csRole`。refresh enrichment 同时修正了 refresh token `id` 与用户 `user_id` 的字段混淆。
- RBAC 写事务先使用统一 PostgreSQL transaction advisory lock，再按 `id` 顺序锁定当前租户 active admins，之后锁目标用户；版本/数据库并发冲突统一映射为 409。测试覆盖 advisory lock、409、rollback 和最后 admin 保护。
- operator RBAC 回归改为通过真实身份解析和 `X-User-Roles`；另有真实请求证明伪造 `X-Operator-Role` 不能提权。
- `029` 幂等迁移补充 `auth.users.tenant_id`、索引和审计租户约束；schema 测试覆盖 Alembic `0023 -> 0024`、原生迁移重复执行和真实 `display_name NOT NULL` 约束。
- `test_auth_session_family.py` 通过真实 `api_key_middleware`、非 skip 的 `/protected-rbac` 路由、`X-API-Key` 和 `JWT_SESSION_GUARD_MODE=enforce` 验证旧 access token：角色变更前 200，变更后实际 middleware `_session_guard` 返回 401，并断言“会话已失效”。
- `test_rbac_api.py` 使用两个独立 psycopg2 连接/事务和与生产一致的 advisory lock、active-admin 固定排序、版本更新及 session/refresh/audit SQL，验证并发最后 admin 降权一方成功、一方 409、线程在超时前结束；schema 不具备时显式 skip。

## TDD 证据

### RED（审查修复前）

先补充/修改测试，再运行：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/customer_service/test_cs_dispatch_schema.py::test_rbac_native_migration_is_idempotent_and_has_no_implicit_tenant_default tests/customer_service/test_cs_dispatch_schema.py::test_p2_migration_chain_is_repeatable_and_real_display_name_constraint_holds -q --no-cov
```

结果：退出码 1，`10 failed, 8 passed`。失败集中在 display_name 未写入、客服权限变化未吊销会话、租户泄漏/默认租户降级、029 缺少用户租户列、最后 admin 并发冲突未映射 409，证明测试确实先于实现暴露缺口。

随后新增跨租户审计隔离断言，先运行该单测得到 `assert [2, 1] == [2]`，再补齐 fake DB 的租户过滤分支；修复后该回归为 `1 passed`。

### 修复轮次 2 RED/GREEN

旧 access token 真实 middleware 证据先运行：

```text
D:/Python/python.exe -m pytest tests/api/test_auth_session_family.py::test_cs_role_change_revokes_real_access_gate_and_refresh_family -q --no-cov
```

首次退出码 1，断言得到 `200 == 401`；日志显示 fake Redis 缺少 `exists`，使真实 `_session_guard` 按 Redis 异常降级放行。补齐 fake 的 `exists` 仅作为 Redis 边界替身能力后，重新运行得到 `1 passed, 1 warning`，并实际经过 middleware 401 分支。

真实 PostgreSQL 并发证据在测试先写入后运行：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py::test_real_postgres_last_admin_concurrency_is_serialized -q --no-cov
1 passed in 11.48s
```

该项是针对已有 advisory-lock 生产实现的真实回归测试，首跑即通过，因此没有伪造一个生产缺陷来制造 RED；PostgreSQL 不可用或所需 schema 缺失时只走明确 `pytest.skip`，连接/事务内的其他异常会进入断言失败。

### GREEN

最终合并回归：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py tests/api/test_operator_role_rbac.py tests/customer_service/test_cs_dispatch_schema.py -q --no-cov
66 passed, 6 warnings in 52.61s
```

分组结果：

```text
D:/Python/python.exe -m pytest tests/customer_service/test_cs_dispatch_schema.py -q --no-cov
15 passed in 10.50s

D:/Python/python.exe -m pytest tests/api/test_auth_session_family.py -q --no-cov
17 passed, 7 warnings in 38.31s

D:/Python/python.exe -m pytest tests/api/test_operator_role_rbac.py -q --no-cov
13 passed in 8.09s
```

warnings 是既有 Windows asyncio Proactor 清理警告，不影响断言或退出码。

## 静态检查

```text
D:/Python/python.exe -m py_compile app/api/routes/rbac.py app/api/routes/auth_local.py app/api/router.py security/session_service.py sql/alembic/memory/versions/0024_rbac_audit.py tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py tests/api/test_operator_role_rbac.py tests/customer_service/test_cs_dispatch_schema.py
exit 0

ruff check app/api/routes/rbac.py app/api/routes/auth_local.py app/api/router.py security/session_service.py sql/alembic/memory/versions/0024_rbac_audit.py tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py tests/api/test_operator_role_rbac.py tests/customer_service/test_cs_dispatch_schema.py
All checks passed!

git diff --check
exit 0
```

本轮新增测试文件的最终静态检查：

```text
ruff check tests/api/test_auth_session_family.py tests/api/test_rbac_api.py
All checks passed!

D:/Python/python.exe -m py_compile tests/api/test_auth_session_family.py tests/api/test_rbac_api.py
exit 0
```

对包含未修改 `backend/app/api/middleware/auth.py` 的扩大 ruff 列表执行时，唯一失败为该既有文件的 `F401 os imported but unused`；本轮未为清理该 P3/既有问题扩大提交范围。

## 修改文件

- `backend/app/api/routes/rbac.py`
- `backend/app/api/routes/auth_local.py`
- `backend/sql/migrations/029_rbac_audit.sql`
- `backend/sql/alembic/memory/versions/0024_rbac_audit.py`（仅 import 格式化）
- `backend/tests/api/test_rbac_api.py`
- `backend/tests/api/test_auth_session_family.py`
- `backend/tests/api/test_operator_role_rbac.py`
- `backend/tests/customer_service/test_cs_dispatch_schema.py`
- 本报告 `task-2-report.md`

本轮新增/修改的工作文件仅为：

- `backend/tests/api/test_auth_session_family.py`
- `backend/tests/api/test_rbac_api.py`
- 本报告 `task-2-report.md`

未修改 `backend/app/api/router.py`、`backend/security/session_service.py` 及 P1 文件；它们已在前一 P2 commit 中实现，本轮只对其做回归验证。

## 迁移假设与验证边界

- 基线为 P1 `0023_cs_dispatch.py` / `028_cs_dispatch.sql`，P2 为 `0024_rbac_audit.py` / `029_rbac_audit.sql`；部署必须先完成 P1，再执行 029。
- `auth.users.tenant_id` 不设隐式 `default`。已有历史用户需要由部署数据迁移/租户治理流程显式回填后才能按租户登录；本任务不猜测其归属。
- P2 schema 测试在临时 PostgreSQL 中实际执行 0024 两次，并验证 `auth.users.tenant_id/version`、`auth.rbac_audits` 和 `cs_agents.display_name NOT NULL`。
- 共享本地数据库直接完整执行 028 时，在既有数据上触发了 P1 的重复 active handoff fail-fast，事务已回滚；本任务没有清理或修改该 P1 数据。因此“共享库完整 028→029 链路”未在本轮验证，属于环境阻塞，必须在干净/已治理的 P1 数据库中由 P9/部署验收补做。029 的独立真实链路已验证。
- 本轮真实 middleware 测试只验证应用内 `api_key_middleware` 与 Redis session guard；APISIX `GATEWAY_SESSION_CHECK` 默认 `audit`、APISIX 到 app 的租户头注入链及网关实际 session 校验仍未作为单元测试验证，保留为 P9/deployment gate。

## 风险

- Redis 只负责即时 access/session 闸门，数据库 session/refresh 吊销是权威；Redis 不可用时会告警并保留 DB 提交，旧 access token 的即时拒绝依赖 Redis 恢复或自然过期。
- `tenant_id` 旧数据的显式回填未包含在本任务，不能把 NULL 解释成 default。
- Windows 测试存在既有 Proactor 清理 warning；当前 focused、auth/operator、P1 schema、py_compile、ruff 均退出成功。
- 共享本地库无法复现完整 028 全量迁移，仅报告为未验证项，不声称已验证。
- advisory lock 当前使用固定全局 key，会把不同租户的 RBAC 写操作一并串行化；本轮不改既有契约，作为 Minor 性能风险记录，后续可评估按租户分片但不得破坏最后 admin 的锁顺序。
- 本轮扩大 ruff 检查仍暴露未修改 middleware 的既有 `F401 os`；修改文件本身 ruff 已通过，未将该清理混入 P2 修复 commit。
