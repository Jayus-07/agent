# 三端账号权限最小可上线改造设计

## 目标与边界

在既有静态 RBAC、JWT、Session、Refresh Token 与 Redis session gate 上新增
`super_admin`，不引入权限表、Casbin、OPA 或任何动态授权框架。平台角色仍是
`viewer < editor < admin < super_admin`；客服角色始终是独立维度
`null | agent | supervisor`，不参与平台角色等级计算。

本次只改授权边界、三端 UX Gate、角色变更后的会话撤销语义与最小 bootstrap。
公开注册仍只能创建 `viewer`。

## 审计结论与兼容约束

- `auth.users.role` 当前是 `VARCHAR(10)` 且有三角色 CHECK；`super_admin`
  为 11 字符，因此迁移必须先将列扩为 `VARCHAR(11)`，再替换 CHECK。
- `SessionService.revoke_user_sessions()` 已在调用事务内撤销所有未撤销
  `auth.sessions` 和目标用户全部 refresh token，并由调用方在 commit 后删除
  Redis session-index 与 access-session keys。此机制保持不变。
- 现有 RBAC 误将 `accepting` 视作客服授权；改为仅 `csRole` 与客服 `enabled`
  触发授权撤销。`maxConversations`、`displayName`、`accepting` 不撤销。
- 现有 APISIX `/api/cs` 规则把平台 `admin` 当客服工作台后门。为遵守最终矩阵，
  本轮收紧为仅 `agent` 或 `supervisor` 可访问客服工作台 API；平台管理员如需
  使用客服工作台，必须明确拥有 `csRole`。

## 设计

### 平台角色与统一守卫

在 `backend/app/api/deps.py` 扩展已知角色和 rank，并通过统一判定让
`admin`、`super_admin` 都满足 admin guard。服务凭据保持 `admin`，绝不映射为
`super_admin`。所有依赖 rank 的 editor/admin 判断随之继承新角色。

APISIX 的 rank 同步加入 `super_admin=3`；客服 `any_of` 则维持独立维度，
只接受 `agent`/`supervisor`，不把客服角色塞入平台 rank。

### RBAC 角色变更

在 `rbac.py` 增加单一 transition helper，于真正更新用户记录前执行：

- 任意 HTTP 请求创建、授予、撤销、降级、禁用或其他修改 `super_admin` 均为 403。
- 普通 `admin` 只能在 `viewer` 和 `editor` 间切换，并可管理独立客服资料、部门
  与普通账号禁用状态。
- 涉及 `admin` 的授予、撤销、降级和禁用仅限 `super_admin`。
- `super_admin` 可创建 `viewer`、`editor`、`admin`，但仍不能由 HTTP 创建
  `super_admin`。

既有“至少保留一个活动的高权限平台管理员”保护扩展为 `admin` 或
`super_admin` 的集合，避免 super_admin 存在时无法按规则降级最后一位普通 admin。

兼容旧的 `/sys/users/{id}/role` 入口，但让它复用同一 transition helper，不留
绕过通道。

### 数据库与 bootstrap

新增一个幂等 SQL migration：扩容 `role`、替换 `ck_users_role`，允许四个角色，
不迁移既有数据。将它登记进 `scripts/init_db.py` 的 migration 单一事实源。

新增显式运维脚本，必须提供 `--tenant` 和二选一的 `--username`/`--user-id`。
脚本只提升已存在用户，写入 `auth.rbac_audits`，撤销其 session/refresh 并在提交后
清理 Redis session keys；不存在用户或角色不匹配均失败，不创建账号、不处理密码。

上线顺序为：先部署兼容四角色的应用与网关，再执行 migration，最后人工运行
bootstrap。回滚前先用受控运维流程将所有 super_admin 降为 admin，随后回退应用；
迁移保留扩展 CHECK 不影响旧应用或旧角色数据。

### 三端 Gate

- 用户端：保留已有 authenticated Gate，所有活跃账号可进入。
- 管理端：AuthGate 成功取得用户信息后，要求平台角色为 `admin` 或
  `super_admin`；否则不渲染管理业务内容，显示无管理端访问权限。
- 客服端：AuthGate 成功取得用户信息后，要求 `csRole` 为 `agent` 或
  `supervisor`；否则不渲染客服业务内容，显示无客服工作台权限。

Gate 仅用于体验，后端 FastAPI guard 与 APISIX/JWT 仍是安全边界。管理端 UI
类型可识别 `super_admin` 以正确显示用户信息，但绝不提供创建或授予该角色的表单选项。

## 验证策略

先为 transition helper、创建接口、会话撤销字段、JWT claim、网关 rank 与三个
AuthGate 写失败测试；确认 RED 后再实现。每个 STOP 仅运行相关单测，最后运行
项目指定的 registry/layer tests、相关 Python API/security tests、三个前端的
Vitest/TypeScript 检查。

最终 HTTP/浏览器验收会覆盖用户、管理、客服三端矩阵，super_admin 登录与 refresh，
角色变更后旧 access/refresh 的失效，以及修改 `accepting` 不退出登录。Docker
Desktop 当前不可用，因此该真实验收在运行环境恢复前不能标记为通过。

## 实施验证记录

- 后端角色、RBAC、会话与一致性套件：`72 passed, 1 skipped`。
- 后端迁移/bootstrap 套件：`5 passed`；`bootstrap_super_admin.py` 通过 `py_compile`。
- 管理端前端：指定 Vitest `9 passed`，TypeScript `npx tsc --noEmit` 退出码 0。
- 客服端前端：指定 Vitest `9 passed`，TypeScript `npx tsc --noEmit` 退出码 0。
- 用户端 AuthGate：指定 Vitest `7 passed`，TypeScript `npx tsc --noEmit` 退出码 0。
- `scripts/init_db.py --port 5433 --check` 已确认迁移目录 57 个、登记 57 个；
  PostgreSQL 连接被拒绝（Docker Desktop 未运行），因此数据库迁移落库与真实 HTTP/浏览器矩阵尚未验证。
