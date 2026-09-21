# 客服高并发派单、RBAC 与登出设计冻结

## 1. 目标与边界

本设计把客服转人工从“用户请求 + 客服手工认领”扩展为可恢复的自动派单链路，并补齐平台 RBAC、客服角色、管理端登出和审计。客服主图 8 个核心节点不改；外部呼叫中心、排班、技能/语言匹配、短信邮件通知和自定义角色设计器不在本阶段。

数据权威保持不变：`customer_service.handoffs` 负责工单生命周期，`assignments` 负责分配历史，`conversations.assigned_agent_id` 是同事务更新的查询投影。Redis 只负责在线候选、ticket 和通知传输，不作为工单状态权威。

## 2. 冻结决策

| 项目 | 冻结值 |
| --- | --- |
| 峰值基线 | 20 个转人工请求/秒、200 个在线用户会话、50 个客服连接 |
| 平台角色 | `viewer`、`editor`、`admin` |
| 客服角色 | `agent`、`supervisor`；通过 `cs_agents.auth_user_id` 绑定用户 |
| 接单机制 | 系统先绑定并创建 offer，客服手动 accept 后进入 `human_active` |
| offer 超时 | 30 秒 |
| 自动重派 | 最多 5 次；总等待 600 秒 |
| 优先级 | 安全/欺诈=100，投诉/争议=80，主动转人工=50，低置信度升级=20 |
| 无人接单 | 关闭本轮人工请求，通知用户并恢复 AI，保留完整审计 |
| 匹配条件 | 首期仅租户、在线、启用、可接单、容量；团队/技能/语言留后续阶段 |
| 自动派单开关 | `off` / `shadow` / `enforce`；关闭时保留一版人工认领兼容接口 |
| 登出 | 撤销当前浏览器 refresh-token family；其他设备不受影响 |
| 通知 | 站内 WebSocket + 浏览器通知 + 轮询补偿 |

上述值是本次实施的默认合同。若业务在迁移或接口发布前修改，必须先更新本文件、状态转换表和对应测试，再继续后续阶段。

## 3. 状态与并发不变量

```text
waiting_human
    └─ dispatcher 原子分配 → agent_offered
          ├─ accept → human_active → close → closed
          ├─ decline/30 秒超时 → waiting_human
          └─ 5 次重派或 600 秒 → closed（恢复 AI）
```

`handoff_requested` 仅作为业务逻辑中的瞬时状态，不作为长期数据库状态。

每次派单在同一 PostgreSQL 事务中完成：

1. 用 `FOR UPDATE SKIP LOCKED` 按 `priority DESC, created_at ASC, id ASC` 锁定一个 `waiting_human` 工单。
2. 从 Redis 读取 45 秒内心跳的客服候选，再用 PostgreSQL 过滤租户、启用、可接单和容量。
3. 按 `active_assignment_count ASC, last_assigned_at ASC NULLS FIRST, agent_id ASC` 锁定客服。
4. 更新 handoff、assignment、conversation 投影并写入带租户、客服、操作者和事件序号的持久事件。
5. 提交后由 relay 发布定向事件；客户端按 `event_id` 去重，重连从“我的 offer”接口补拉。

正确性由行锁、版本谓词、部分唯一索引和事务保证；不使用 Redis 分布式锁。

## 4. 接口边界

### 用户入池

`POST /api/cs/conversations/{conversation_id}/handoff`

- 必须携带 `Idempotency-Key`。
- `user_id`、`tenant_id` 从已认证请求上下文取得，路由不得接受客户端覆盖值。
- 同租户同会话已有活动 handoff 时返回同一 `handoff_id`，并发请求不得产生第二条活动工单。
- 跨用户、跨租户或会话不存在返回拒绝；数据库不可用时不得返回“成功入池”。

### 坐席与主管

- agent 只能查看、accept、decline、回复和关闭自己的 offer/会话。
- supervisor 额外可查看租户队列、调整本人可用状态和容量、执行主管重派。
- 所有 agent 身份从 JWT 绑定的 `cs_agents.auth_user_id` 推导，浏览器不再提交可信 `agent_id`。
- 旧版本/过期 assignment 的写操作返回 409；非拥有者返回 403。

### RBAC 与会话

- `/api/sys/rbac/users`：分页、搜索用户；仅 admin。
- `/api/sys/rbac/users/{user_id}/role`：平台角色、客服角色、容量与启用状态修改；携带版本谓词，冲突 409。
- `/api/sys/rbac/audit`：角色和客服档案审计；仅 admin。
- 最后一个 admin 不能降级或禁用。
- 角色变更在同一事务撤销目标用户活动 session；旧 access token 下一次请求和旧 refresh token 刷新都返回 401。
- 管理端登出只调用一次，清理本地 token/user、React Query、客服 WebSocket 后跳转 `/login`。

## 5. 数据与事件

P1 为 `handoffs`、`cs_agents`、`assignments`、`events` 增加租户、offer、版本、状态和 outbox 所需字段；所有活动唯一性索引包含租户语义。迁移必须同时支持空库和存量库，原生 SQL 可重复执行，升级前检查活动工单和 assignment 重复数据。

事件至少包含：`event_id`、`tenant_id`、`handoff_id`、`conversation_id`、`event_seq`、事件类型、操作者、旧/新状态和创建时间。relay 只有在 Redis 发布成功后标记 published；重复投递由 `event_id` 幂等。

## 6. 工作树与迁移基线

实现使用 `codex/cs-dispatch-rbac-logout` 隔离工作树。原工作树已有用户未提交改动；为复现其当前开发基线，已将与本方案有接口关系的迁移目录（Alembic `0014–0022`、原生 `027`）和被忽略的 `frontend-admin/src/lib` 镜像到隔离工作树。它们不是本分支本任务的改动，不得加入本分支提交。

因此本方案的新增迁移采用计划指定的 Alembic `0023_cs_dispatch` 与原生 `028_cs_dispatch.sql`。若最终集成目标不包含现有 `0014–0022`/`027`，集成前必须先重排迁移基线；不得在生产直接执行有歧义的 revision 链。

## 7. 基线证据（2026-09-20）

- 后端：`cd backend && D:/Python/python.exe -m pytest tests/customer_service tests/api/test_auth_session_family.py tests/api/test_operator_role_rbac.py tests/security/test_local_jwt.py -q --no-cov` → `840 passed, 2 skipped, 4 warnings`。
- 用户端：`frontend/npm test -- --run` → `15` 个测试文件、`234` 个测试通过。
- 管理端（镜像当前忽略库后）：`frontend-admin/npm test -- --run` → `32` 个测试文件、`318` 个测试通过。
- TypeScript：用户端与管理端 `npx tsc --noEmit` 均退出码 0。
- 后端警告为 Windows asyncio Proactor transport 清理时的既有 `PytestUnraisableExceptionWarning`，未改变通过结果。
