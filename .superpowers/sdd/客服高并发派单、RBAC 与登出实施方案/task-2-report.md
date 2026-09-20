# Task 2 修复报告：P2 RBAC 与会话后端

## 状态

已针对审查意见完成 P2 修复，未修改 P3 文件、前端、原工作树、P1 模型/迁移或用户的 `0014–0022/027` overlay。修复 commit message：`fix: close P2 RBAC review gaps`。

## 本轮修复内容

- `rbac.py` 创建客服档案时写入稳定的 `display_name`：优先使用服务端 `real_name`，其次 `username`，最后回退为 `user-{id}`；新增真实非空约束回归测试，并将返回对象带上显示名。
- `csRole`、客服档案 `enabled`、`accepting` 变化与平台角色/禁用变化一样，在同一个数据库事务中吊销目标用户的 session 与全部 refresh token family；事务提交后清理 Redis access/session 闸键。行为测试断言 DB session、refresh token、Redis 闸键和旧 refresh 请求均失效，而非只断言调用次数。
- 租户从可信 identity/header 契约读取，缺失时拒绝，不再回退 `default`。用户列表、更新、审计查询均带租户条件；登录/refresh 按租户查询并返回真实 `tenantId/csRole`。refresh enrichment 同时修正了 refresh token `id` 与用户 `user_id` 的字段混淆。
- RBAC 写事务先使用统一 PostgreSQL transaction advisory lock，再按 `id` 顺序锁定当前租户 active admins，之后锁目标用户；版本/数据库并发冲突统一映射为 409。测试覆盖 advisory lock、409、rollback 和最后 admin 保护。
- operator RBAC 回归改为通过真实身份解析和 `X-User-Roles`；另有真实请求证明伪造 `X-Operator-Role` 不能提权。
- `029` 幂等迁移补充 `auth.users.tenant_id`、索引和审计租户约束；schema 测试覆盖 Alembic `0023 -> 0024`、原生迁移重复执行和真实 `display_name NOT NULL` 约束。

## TDD 证据

### RED（审查修复前）

先补充/修改测试，再运行：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/customer_service/test_cs_dispatch_schema.py::test_rbac_native_migration_is_idempotent_and_has_no_implicit_tenant_default tests/customer_service/test_cs_dispatch_schema.py::test_p2_migration_chain_is_repeatable_and_real_display_name_constraint_holds -q --no-cov
```

结果：退出码 1，`10 failed, 8 passed`。失败集中在 display_name 未写入、客服权限变化未吊销会话、租户泄漏/默认租户降级、029 缺少用户租户列、最后 admin 并发冲突未映射 409，证明测试确实先于实现暴露缺口。

随后新增跨租户审计隔离断言，先运行该单测得到 `assert [2, 1] == [2]`，再补齐 fake DB 的租户过滤分支；修复后该回归为 `1 passed`。

### GREEN

最终合并回归：

```text
D:/Python/python.exe -m pytest tests/api/test_rbac_api.py tests/security/test_session_service.py tests/api/test_auth_session_family.py tests/api/test_operator_role_rbac.py tests/customer_service/test_cs_dispatch_schema.py -q --no-cov
65 passed, 8 warnings in 42.67s
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

未修改 `backend/app/api/router.py`、`backend/security/session_service.py` 及 P1 文件；它们已在前一 P2 commit 中实现，本轮只对其做回归验证。

## 迁移假设与验证边界

- 基线为 P1 `0023_cs_dispatch.py` / `028_cs_dispatch.sql`，P2 为 `0024_rbac_audit.py` / `029_rbac_audit.sql`；部署必须先完成 P1，再执行 029。
- `auth.users.tenant_id` 不设隐式 `default`。已有历史用户需要由部署数据迁移/租户治理流程显式回填后才能按租户登录；本任务不猜测其归属。
- P2 schema 测试在临时 PostgreSQL 中实际执行 0024 两次，并验证 `auth.users.tenant_id/version`、`auth.rbac_audits` 和 `cs_agents.display_name NOT NULL`。
- 共享本地数据库直接完整执行 028 时，在既有数据上触发了 P1 的重复 active handoff fail-fast，事务已回滚；本任务没有清理或修改该 P1 数据。因此“共享库完整 028→029 链路”未在本轮验证，属于环境阻塞，必须在干净/已治理的 P1 数据库中由 P9/部署验收补做。029 的独立真实链路已验证。

## 风险

- Redis 只负责即时 access/session 闸门，数据库 session/refresh 吊销是权威；Redis 不可用时会告警并保留 DB 提交，旧 access token 的即时拒绝依赖 Redis 恢复或自然过期。
- `tenant_id` 旧数据的显式回填未包含在本任务，不能把 NULL 解释成 default。
- Windows 测试存在既有 Proactor 清理 warning；当前 focused、auth/operator、P1 schema、py_compile、ruff 均退出成功。
- 共享本地库无法复现完整 028 全量迁移，仅报告为未验证项，不声称已验证。
