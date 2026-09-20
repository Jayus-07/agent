# 客服高并发派单、RBAC 与登出实施方案

## 一、假设与待确认问题

### 默认假设

| ID   | 假设      | 本方案采用值                                                 |
| ---- | --------- | ------------------------------------------------------------ |
| A1   | 容量基线  | 峰值 20 个转人工请求/秒、200 个在线用户会话、50 个客服 WebSocket 连接 |
| A2   | 租户模型  | 按现有项目决策执行多租户隔离；新增查询、索引和唯一约束全部包含 `tenant_id` |
| A3   | RBAC 范围 | 保留平台 `viewer/editor/admin` 三角色；客服域另设 `agent/supervisor` 坐席角色，不开放自定义角色设计器 |
| A4   | 接单机制  | 系统先绑定并向客服发出 offer，客服必须手动接单；未接单不进入 `human_active` |
| A5   | 超时参数  | 接单超时 30 秒；最多自动重派 5 次；总等待上限 600 秒         |
| A6   | 通知渠道  | 首期只做站内 WebSocket、浏览器桌面通知和轮询补偿，不接短信、邮件、企微 |
| A7   | 登出范围  | 普通登出撤销当前浏览器的整个 refresh-token family；其他设备保持在线 |
| A8   | 部署方式  | 允许新增独立 `cs-dispatcher` 容器，至少运行 2 个副本         |
| A9   | 唯一入口  | 所有验收请求均经过 APISIX `:9080`，不以直连 FastAPI `:8000` 作为上线验收 |

### 待确认问题

| ID   | 问题                                                         | 推荐默认                                          |      | 是否阻断上线     |
| ---- | ------------------------------------------------------------ | ------------------------------------------------- | ---- | ---------------- |
| Q1   | 实际峰值是否超过 20 工单/秒、50 名在线客服？                 | 按 A1                                             |      | 是               |
| Q2   | “RBAC 配置”是否要求管理员创建任意角色和权限码？              | 不要求；仅配置固定平台角色、客服角色和容量        |      | 是               |
| Q3   | 30 秒接单超时、5 次重派、10 分钟总等待是否可接受？           | 接受                                              |      | 是               |
| Q4   | 优先级映射是否采用：安全/欺诈=100、投诉/争议=80、主动转人工=50、低置信度升级=20？ | 采用                                              |      | 是               |
| Q5   | 10 分钟仍无人接单后如何处理？                                | 关闭本轮人工请求，通知用户并恢复 AI；保留工单审计 |      | 是               |
| Q6   | 客服是否按团队、技能、语言匹配？                             | 首期只按租户、在线、可接单、容量过滤              |      | 否，作为后续阶段 |
| Q7   | 是否允许自动派单关闭后回退当前“人工认领”模式？               | 允许，保留一版兼容接口                            |      | 是               |

## 二、现状判断

现有代码可以复用，但尚不能直接满足生产自动派单：

- 已有 PostgreSQL 转人工记录、客服档案、最大会话数和 assignment 历史；但没有自动调度与 offer 状态。[agent.py (line 16)](D:/Program Files/workplace/agent/backend/customer_service/models/agent.py:16)
- 当前认领使用条件更新，能防止同一工单被多个客服同时抢到；它可以作为原子绑定实现的基础。[cs_admin.py (line 712)](D:/Program Files/workplace/agent/backend/app/api/routes/cs_admin.py:712)
- 当前队列按更新时间取单，没有优先级、在线过滤、容量过滤和轮询公平性。[cs_admin.py (line 555)](D:/Program Files/workplace/agent/backend/app/api/routes/cs_admin.py:555)
- WebSocket ticket 和连接集合保存在单进程内存中，多 API 实例下 ticket 可能在另一个实例核销失败，且事件会广播给所有客服。[realtime.py (line 57)](D:/Program Files/workplace/agent/backend/customer_service/realtime.py:57)
- 坐席页面仍允许手工输入 `agent_id` 并主动认领，不符合生产身份可信边界。[page.tsx (line 107)](D:/Program Files/workplace/agent/frontend-admin/src/app/cs/handoff/page.tsx:107)
- 后端登出、token 吊销已经存在，前端认证库也已有 `logout()`；缺少管理端可见的登出入口、缓存清理和完整端到端验证。[auth_local.py (line 404)](D:/Program Files/workplace/agent/backend/app/api/routes/auth_local.py:404)、[auth.ts (line 154)](D:/Program Files/workplace/agent/frontend-admin/src/lib/auth.ts:154)
- RBAC 当前只有单列 `auth.users.role` 和角色变更接口，没有用户列表、客服角色配置、审计与管理页面。[auth_local.py (line 470)](D:/Program Files/workplace/agent/backend/app/api/routes/auth_local.py:470)

## 三、目标与成功标准

| 目标           | 可检查成功标准                                               |
| -------------- | ------------------------------------------------------------ |
| 工单创建正确   | 同一租户、同一会话并发提交 100 次，只产生 1 条活动工单；其余请求返回同一 `handoff_id` |
| 高并发可用     | 生产等规格环境持续 20 请求/秒运行 10 分钟，HTTP 5xx 比例 `<0.1%`，创建接口 P95 `<300ms`、P99 `<800ms` |
| 优先级取单     | 单 dispatcher 场景严格按 `priority DESC, created_at ASC, id ASC` 处理；存在更高优先级可派工单时，不得先派低优先级 |
| 最少负载与轮询 | 客服选择顺序固定为 `active_assignment_count ASC, last_assigned_at ASC NULLS FIRST, agent_id ASC`；500 次派单后，同容量客服负载最大差值 `≤1` |
| 不超载         | 任意压测时刻，每名客服活动 assignment 数 `≤max_conversations`；违规次数必须为 0 |
| 原子绑定       | 100 个并发 dispatcher 对同一工单执行分配，只能产生 1 条活动 assignment、1 个 `assigned_agent_id` |
| 通知及时       | 有可用客服时，从工单事务提交到目标客服收到 offer：P95 `≤1 秒`、P99 `≤2 秒` |
| 超时重派       | offer 到期后 `≤2 秒`完成释放；旧 assignment 关闭、客服负载释放一次、工单进入下一轮派单 |
| 故障恢复       | dispatcher 在事务提交后、通知前被杀死，另一实例能从持久事件中恢复通知；不得重复绑定 |
| 权限安全       | 客户只能读本人会话；客服只能处理分给自己的工单；主管可查看池和重派；管理员可配置 RBAC；跨用户、跨客服、跨租户越权用例拒绝率 100% |
| 登出闭环       | 点击登出后本地 token/user 缓存清除并跳转 `/login`；旧 access token 下一次请求返回 401；旧 refresh token 刷新返回 401 |
| 可观测         | 每条工单能由 `tenant_id + handoff_id` 关联创建、派单、offer、接单、重派、关闭、操作者和事件序号；字段完整率 100% |

## 四、范围边界

| 类型     | 明确边界                                                     |
| -------- | ------------------------------------------------------------ |
| 做       | 用户端显式转人工 API、幂等入池、优先级队列、客服在线状态、容量过滤、最少负载/轮询、原子绑定、定向通知、接单/拒绝、超时回收重派、主管重派、RBAC 管理页、管理端登出、审计、指标、故障与压力测试 |
| 不做     | 外部呼叫中心、排班系统、客服绩效结算、AI 技能匹配、多语言匹配、短信/邮件/企微通知、任意自定义角色设计器、修改主图 8 个核心节点 |
| 兼容保留 | 当前人工认领接口保留一个发布周期，仅在自动派单关闭或主管人工干预时启用 |
| 数据权威 | `customer_service.handoffs` 是工单生命周期唯一事实源；`assignments` 是分配历史；`conversations.assigned_agent_id` 是同事务更新的查询投影 |
| 停止实施 | 相关未提交改动所有权未确认；迁移发现活动工单重复；`tenant_id` 无法从 JWT 可信获得；生产不允许新增 dispatcher；Q2/Q3/Q4/Q5 未确认 |
| 停止发布 | 任一重复绑定、超载、跨租户泄漏发生；旧 token 登出后仍能访问；压力指标不达标；迁移回放不通过 |

## 五、总体做法

### 方案比较

| 方案                                                 | 结论       | 原因                                                         |
| ---------------------------------------------------- | ---------- | ------------------------------------------------------------ |
| PostgreSQL 工单池 + 独立 dispatcher + Redis 在线状态 | **推荐**   | 与现有技术栈一致；数据库锁和唯一约束保证正确性；Redis 故障时可以安全停止派单而不丢工单 |
| Redis 队列直接派单                                   | 不采用     | 工单、会话、分配需要跨 Redis/PG 双写，恢复与审计复杂，容易出现已通知但未落库 |
| 保留人工抢单，只给队列排序                           | 不满足需求 | 没有自动筛选、自动绑定和超时重派                             |

### 状态机

```
waiting_human
    │ dispatcher 原子分配
    ▼
agent_offered ──客服接单──▶ human_active ──结束──▶ closed
    │
    ├─拒绝/30 秒超时──▶ waiting_human
    └─超过 5 次或 600 秒──▶ closed（通知用户并恢复 AI）
```

`handoff_requested` 只作为业务逻辑中的瞬时状态，不再作为数据库可长期停留状态。

### 原子派单事务

每次派单必须在一个 PostgreSQL 事务内完成：

1. 按优先级用 `FOR UPDATE SKIP LOCKED` 锁定一条 `waiting_human` 工单。
2. 从 Redis 取得 45 秒内有心跳的在线客服 ID。
3. 在 PostgreSQL 中过滤同租户、启用、可接单且活动 assignment 数小于 `max_conversations` 的客服。
4. 按活动负载、上次派单时间、客服 ID 排序，锁定一名客服。
5. 将工单改为 `agent_offered`，写入客服、版本号和 30 秒过期时间。
6. 新增 `offered` assignment，更新 conversation 投影。
7. 同事务写入定向事件；提交后由事件 relay 发布到 Redis。
8. 客户端按 `event_id` 去重；重连时从“我的 offer”接口补拉。

不用 Redis 分布式锁；正确性由数据库行锁、部分唯一索引和事务保证。

### RBAC 模型

采用两个正交维度：

- 平台角色：`viewer / editor / admin`
- 客服角色：`agent / supervisor`

权限矩阵：

| 身份       | 权限                                                         |
| ---------- | ------------------------------------------------------------ |
| viewer     | 现有只读管理能力                                             |
| editor     | viewer + 知识/Prompt 编辑能力                                |
| admin      | 全部平台配置、RBAC、客服主管能力                             |
| agent      | 设置本人在线状态、查看/接收/拒绝本人 offer、回复和关闭本人会话 |
| supervisor | agent + 查看工单池、手工重派、配置坐席容量与可用状态         |

客服身份通过 `cs_agents.auth_user_id` 绑定登录用户；浏览器不再提交可信的 `agent_id`。

## 六、分阶段步骤

| 阶段                   | 关键动作                                                     | 主要产出                                                     | 完成标准                                                     |
| ---------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| P0 基线与决策冻结      | 确认 Q1–Q7；盘点当前未提交改动；冻结 API、状态机、优先级和超时参数 | 设计文档、接口契约、状态转换表、文件所有权清单               | 问题全部有书面答案；相关文件无不明归属改动；现有客服/auth 测试结果已记录 |
| P1 数据模型与迁移      | 扩展 `handoffs/cs_agents/assignments/events`；增加 `tenant_id`、offer 字段、客服用户绑定、assignment 状态、定向事件、版本号和部分唯一索引 | Alembic `0023_cs_dispatch`、原生迁移 `028_cs_dispatch.sql`、ORM 与迁移测试 | 空库升级成功；存量库升级成功；重复执行原生迁移不报错；升级前后行数一致；活动工单和 assignment 重复数均为 0 |
| P2 RBAC 与会话后端     | 新建 `routes/rbac.py`、RBAC service/repository；提供分页用户列表、平台角色修改、客服档案及容量配置、审计；登录/刷新返回完整角色；角色变化强制撤销目标用户活动会话 | `/api/sys/rbac/users`、`/roles`、`/audit`；客服权限依赖；会话撤销服务 | 非 admin 全部 403；最后一个 admin 不能被降级/禁用；并发版本冲突返回 409；角色变更后旧 token 下一请求 401 |
| P3 RBAC 页面与登出     | 新建 `/settings/access`；实现用户、平台角色、客服角色、容量和启用状态配置；Sidebar 增加用户菜单和登出按钮；修复静默刷新后角色缓存 | RBAC 页面、审计页签、`SidebarUserMenu`、登出交互测试         | 管理员可分页/搜索/修改；viewer/editor 直输 URL 显示 403；登出请求只发一次，本地态清除、React Query 清空、WS 关闭并跳 `/login` |
| P4 用户入池链路        | 新增 `POST /cs/conversations/{id}/handoff`；要求 `Idempotency-Key`；服务端取可信 user/tenant；前端按钮改为直接调用，不再靠发送自然语言触发 | 幂等创建 API、用户端状态卡、重复点击保护                     | 100 次并发点击只生成 1 条工单；PG 不可用时成功写入数为 0；未认证 401、跨用户 403 |
| P5 在线状态与多实例 WS | ticket 改存 Redis 并用原子 GET+DEL 核销；ticket 绑定客服与租户；Hub 按客服定向连接；15 秒心跳、45 秒在线 TTL；断线保留轮询补偿 | presence service、跨实例 WS ticket、定向事件协议             | HTTP ticket 在 API-A 签发、API-B 核销成功且仅一次；客服 A 收不到客服 B 的 offer；Redis 故障时不产生新绑定 |
| P6 自动 dispatcher     | 新建 `customer_service/dispatch/` 和 `workers/cs_dispatcher.py`；实现优先级取单、在线/容量过滤、最少负载轮询、事务绑定；Compose 增加 2 副本与健康检查 | dispatcher 服务、调度 repository/service、配置项、Docker 服务 | 100 路并发派单无重复、无超载；同容量客服 500 次分配负载差 `≤1`；dispatcher 心跳中断 30 秒内告警 |
| P7 接单、拒绝与重派    | 新增本人 offer 列表、accept/decline API；重写坐席工作台为“待接单/处理中”；去掉手工 agent ID；每秒回收过期 offer并排除刚超时客服 | 新坐席工作台、offer 倒计时、超时 reaper、主管重派            | 过期后 2 秒内释放并重派；旧版本 accept 返回 409；非被分配客服 accept/reply/close 全部 403/409 |
| P8 事件可靠性与观测    | 将 `events` 扩展为持久 outbox；状态事务内落事件，relay 至 Redis，成功后标记 published；增加队列、等待、派单、超时、在线客服、outbox lag 指标 | 可靠事件 relay、Prometheus 指标、告警规则、运营统计          | 杀死 dispatcher 后事件不丢；重复投递由 `event_id` 去重；outbox lag P99 `<2 秒`；所有审计字段完整 |
| P9 压测、故障演练      | 通过 APISIX 运行 200 并发突发和 20 RPS×10 分钟持续测试；演练 Redis/PG/dispatcher/API 实例故障；按 shadow→5%→20%→50%→100% 放量 | 压测脚本、结果 JSON、故障报告                                | 每档至少运行 24 小时且累计至少 200 个转人工工单；全部成功标准通过才升档；任一数据错误立即切 `off` 并停 dispatcher |

## 七、主要代码落点

新增：

- `backend/customer_service/dispatch/service.py`
- `backend/customer_service/dispatch/repository.py`
- `backend/customer_service/dispatch/presence.py`
- `backend/customer_service/dispatch/event_relay.py`
- `backend/workers/cs_dispatcher.py`
- `backend/config/cs_dispatch.py`
- `backend/app/api/routes/cs_dispatch.py`
- `backend/app/api/routes/rbac.py`
- `backend/security/session_service.py`
- `frontend-admin/src/app/settings/access/page.tsx`
- `frontend-admin/src/api/rbac.ts`
- `frontend-admin/src/components/layout/SidebarUserMenu.tsx`

重点修改：

- `backend/customer_service/models/{handoff,agent,assignment,event}.py`
- `backend/customer_service/handoff.py`
- `backend/customer_service/maintenance.py`
- `backend/customer_service/realtime.py`
- `backend/app/api/routes/auth_local.py`
- `backend/app/api/routes/cs_admin.py`
- `backend/security/local_jwt.py`
- `frontend/src/api/cs.ts`
- `frontend/src/components/cs/CSWelcome.tsx`
- `frontend/src/components/cs/CSHandoffCard.tsx`
- `frontend-admin/src/app/cs/handoff/page.tsx`
- `frontend-admin/src/lib/auth.ts`
- `frontend-admin/src/lib/csAgentWs.ts`
- `frontend-admin/src/components/layout/navConfig.tsx`
- `docker-compose.yml`

## 八、依赖与风险

| 依赖/风险                            | 触发后果                     | 控制措施                                                     | 验证方式                       |
| ------------------------------------ | ---------------------------- | ------------------------------------------------------------ | ------------------------------ |
| 当前工作树存在大量未提交修改         | 覆盖其他工作或产生错误基线   | 实施时使用独立 worktree；相关文件先做所有权清单              | P0 清单无未确认项              |
| Redis 在线状态与 PG 事务不可原子提交 | 已离线客服可能收到一次 offer | Redis 只判断候选；容量和绑定以 PG 为准；30 秒 offer 自动回收 | 断网/杀浏览器演练              |
| 进程内 HandoffStore 缓存跨实例失效   | 用户或客服读取旧状态         | 工单关键状态取消 L1 权威读，统一读 PG                        | 两 API 实例交叉读写测试        |
| dispatcher 多副本竞争                | 重复派单或超载               | `FOR UPDATE SKIP LOCKED`、活动 assignment 部分唯一索引、固定锁顺序 | 100 路并发 PG 测试             |
| 通知发布前进程崩溃                   | 工单已绑定但客服不知情       | 事务内持久事件；另一 relay 重发；客户端补拉                  | 提交后 kill -9 故障注入        |
| 角色缓存过期                         | 已降权用户继续操作           | 修改角色后撤销目标用户所有活动 session；刷新响应始终更新本地 userInfo | 角色变更后的旧 token 测试      |
| 存量客服无法映射 auth 用户           | dispatcher 找不到可用客服    | 迁移后默认不参与自动派单；管理员先完成绑定                   | 上线前未绑定启用客服数必须为 0 |
| 数据库连接耗尽                       | API 与 dispatcher 同时 503   | dispatcher 独立小连接池；所有实例连接上限总和不得超过 PG `max_connections` 的 70% | 启动校验和连接数压测           |
| 多租户漏条件                         | 严重数据泄漏                 | repository 方法强制 tenant 参数；复合唯一索引；禁止路由自行拼 SQL | 跨租户测试拒绝率 100%          |
| 自动派单算法异常                     | 影响在线客服                 | `off/shadow/enforce` 三态开关；人工认领接口保留一版          | 30 秒内完成开关回退            |

## 九、验收清单

- 

  同会话 100 次并发转人工只生成 1 条活动工单。

- 

  工单顺序符合优先级和 FIFO 规则。

- 

  50 名同容量客服完成 500 次派单后负载差不超过 1。

- 

  任一客服活动 assignment 数不超过 

  ```
  max_conversations
  ```

  。

- 

  同工单不存在两条活动 assignment。

- 

  客服离线、禁用或满载时不会被绑定。

- 

  offer 拒绝或 30 秒未接后 2 秒内释放并重派。

- 

  客服只能处理分给自己的工单。

- 

  supervisor 可重派，agent 不可重派。

- 

  跨用户、跨客服、跨租户访问全部被拒绝。

- 

  WS ticket 可跨两个 API 实例一次性核销。

- 

  Redis 故障期间工单保留在 PG，恢复后 2 秒内继续派单。

- 

  dispatcher 崩溃恢复后无重复绑定、无丢通知。

- 

  RBAC 页面只有 admin 可访问。

- 

  最后一个 admin 无法被降级或禁用。

- 

  角色变更后旧会话立即失效。

- 

  管理端登出后旧 access/refresh token 均不可继续使用。

- 

  前后端 TypeScript、Vitest、客服后端测试、认证测试和注册一致性测试全部通过。

- 

  所有业务 E2E 请求均经过 APISIX。

- 

  ```
  devctl.bat restart all /y
  ```

   后服务和 dispatcher 健康检查全部通过。

  

## 十、下一步行动

1. 先确认 Q1–Q7，尤其是动态 RBAC、超时参数、无人接单终态和是否允许新增 dispatcher。
2. 确认后将方案固化为设计文档和逐任务 TDD 实施计划。
3. 在独立 worktree 中执行 P0，记录当前测试基线和迁移前数据检查结果。
4. 依次实施 P1–P8；任何阶段未达到完成标准不得进入下一阶段。
5. P9 先在预发布环境压测和故障演练，通过后再开始生产。