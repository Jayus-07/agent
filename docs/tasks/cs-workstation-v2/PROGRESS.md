# PROGRESS — cs-workstation-v2（v2 重建版）

- 任务文件：同目录 TASK.md（v2 重建计划的唯一事实源）；原型：`prototypes/agent-workstation.html`、`prototypes/user-portal.html`
- 更新时间：2026-10-31
- 当前状态：**v2 重建计划已落稿，等待执行会话接手**（启动清单见 TASK.md 第 9 节）；任务文件与原型未提交（P0 首件事=经用户确认提交基线）
- v1→v2 变更：2026-10-31 用户决策——不复用原有 CS 业务组件，对照原型全量重建；v1 增量计划（原 P0–P7）作废，差距核对结论保留于 TASK.md 第 10 节

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 | 更新时间 |
|---|---|---|---|---|---|
| P0 | 脚手架与基线（cs2 命名空间/路由壳/pending 快照） | 已完成 | feat/cs-v2-p0 → PR #147（merge 4b80207） | 详见下方日志 2026-10-31（五） | 2026-10-31 |
| P1 | 会话基础链路（双端对话/WS/时间线） | 计划中 | - | - | - |
| P2 | 交易与提案（商城三页+资金闭环+确认卡+取消订单+余额） | 计划中 | - | - | - |
| P3 | 转人工与坐席运营（接入页/状态/转接/SLA/接管后输入） | 计划中 | - | - | - |
| P4 | 快捷回复+AI Copilot | 计划中 | - | - | - |
| P5 | 退款审核闭环 | 计划中 | - | - | - |
| P6 | 质检中心 | 计划中 | - | - | - |
| P7 | 看板+知识助手 | 计划中 | - | - | - |
| P8 | 切换与清理（路由切换/删旧/矩阵终验） | 计划中 | - | - | - |

状态取值：计划中 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每期完成后必须回填：分支与 PR、实际测试命令与 PASS/FAIL/SKIP、重建与健康检查结果、原型矩阵对应项勾选证据、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-cs-v2`（分支 feat/cs-workstation-v2，自 main@12bdef8）
- 原型：`prototypes/agent-workstation.html`（坐席端，129KB）、`prototypes/user-portal.html`（用户端，69KB）——浏览器直接打开对照
- .env：worktree 默认没有（gitignore），需要 compose/SSRF 开关测试时从主仓库拷贝
- 主栈端口占用：8000 / 8090 / 8091 / 9080 / 3300 / 5433；并行验证用错位端口；连库显式 5433
- 演示数据底座：agent_business 库 customer 99001「演示用户（模拟数据）」，订单 DEMO-1001/1002/1003/1006/1010/1012

## 交接备忘（给执行会话）

1. 启动清单：TASK.md 第 9 节（worktree 核对 → 通读任务文件+原型 → 从最早「计划中」期开始）。
2. 铁律：前端新组件树禁 import 旧 CS 组件；后端只增不改坏；事件只追加；每期合入前旧路径冒烟+新页验收双绿。
3. 本会话遗留：无未提交代码（仅 docs/tasks 与 prototypes 两处文档/原型）；无容器/端口占用。

## 日志

### 2026-10-31（一）
- 完成企业级差距分析，创建任务文件与 worktree（v1 增量计划 P0–P7）。

### 2026-10-31（二）
- 组件级差距核对（10 类场景）：最大断层为退款审核闭环；总计划曾新增 P4.5、P0 增补 pending 可见性、P8 候选池落档（后被 v2 重构吸收）。

### 2026-10-31（三）
- 原型定稿并入库 prototypes/；坐席端补退款审核页/pending 可见性/余额；用户端目标态变更（接管后可输入）。

### 2026-10-31（四）
- **用户决策：不复用原有 CS 业务组件，对照原型全量重建。** TASK.md 重写为 v2 重建版（P0–P8 九期，含原型功能验收矩阵 3.1/3.2、重建边界第 2/6 节、交接清单第 9 节）；v1 增量计划作废，差距核对保留（第 10 节）。
- 等待执行会话接手，从 P0 开始。

### 2026-10-31（五）—— P0 实现完成，待验收

- **分支**：feat/cs-v2-p0（自 12bdef8 建分支，期间 main 前移至 33335d5，已 fast-forward 对齐；改动文件与 main 新提交零重叠）。
- **后端**：`cs_admin.py` 新增 `GET /cs/conversations/{id}/pending-proposal` 坐席侧只读快照（可见性闸 `_ensure_pending_proposal_access`：supervisor/admin 本租户全量、agent 仅当前认领会话、api-key 服务通道放行、普通用户 403；口径与用户侧 `/my/{id}/pending` 同源：load_authoritative + handoff 暂停 + 过期判定；summary 过 PII 脱敏、target_id 保留原值供坐席关联上下文）。零迁移、零表变更。
- **前端**：frontend-cs/src/cs2（nav/WorkstationShell/HealthBadge/PagePlaceholder + /cs2/{workspace,conversations,tickets,refunds,qa,dashboard,knowledge} + app/cs2/layout，root layout 将 /cs2 排除出旧 Sidebar）；frontend/src/cs2 + /customer-service-v2、frontend/src/mall + /mall、/my-orders（root layout isStandalone 增补三路由）。全部空壳 + 健康自检（/api/health 经 BFF）。
- **测试证据**：
  - 新 API T2：`pytest backend/tests/api/test_cs_pending_proposal_agent_api.py` 14 passed（supervisor/admin/认领坐席放行、未认领/跨租户/普通用户/未认证拒绝、服务通道放行、空快照、handoff 暂停、过期、404、store 故障 fail-closed）。
  - 相邻回归：`test_cs_pending_api.py + test_cs_admin_idor_gate.py + test_cs_admin_api.py` 48 passed（其 1 例 `test_returns_owner_scoped_safe_snapshot_and_masks_pii` 在 12bdef8 上即失败——290d2b7 引入 reason 字段未同步断言，main@33335d5 已修复，现全绿）。
  - 双端 vitest 全量（T0 旧功能冒烟）：frontend-cs 38 files/289 passed；frontend 82 files/669 passed。cs2/mall 定向 7+6 passed（含 no-legacy-imports 守卫）。
  - tsc --noEmit：frontend-cs 0 错误；frontend 仅存量 16 例（bff-proxy.test.ts 14 + store/chat.test.ts 2，main 基线相同，与本任务无关）；新增文件 0 错误。
  - grep 断言：cs2/mall 新命名空间零旧组件引用（双端守卫测试 + grep 双确认）。
- **重建与健康**：共享主栈以本 worktree 代码重建（COMPOSE_PROJECT_NAME=agent，app+11 后端服务+frontend 重建；frontend-admin 未受影响未动）；全部 healthy；HTTP 冒烟：/health、:8081/health、:3100/login、:3200/login 均 200；新端点未认证 401（挂载+闸生效）；新路由 /customer-service-v2、/mall、/my-orders 200；旧路由 /customer-service、/ 200 零回归。frontend-cs `npm run build` 成功，7 条 /cs2/* 路由与旧 /cs/* 并行产出。
- **基线核对回写**：迁移实况最新 099（双 082），新迁移自 100+ 顺延，TASK.md 第 4/5/6 节已更正。
- **环境备注**：worktree .env 已从主仓库拷贝（compose 需要）；frontend-cs 不在共享栈容器口径内（devctl rebuild 只重建 frontend-admin/frontend），其验证以 vitest/tsc/生产构建为准。
- **交付记录**：用户确认后按路径限定分两笔提交（545f042 任务基线文档、69356cf P0 代码），PR #147 CI 全绿（scan / RAG Smoke / RAG 变更检测）后合并 main（merge commit 4b80207）。下一期 P1 自 origin/main@4b80207 起新分支 feat/cs-v2-p1。
