# PROGRESS — cs-workstation-v2（v2 重建版）

- 任务文件：同目录 TASK.md（v2 重建计划的唯一事实源）；原型：`prototypes/agent-workstation.html`、`prototypes/user-portal.html`
- 更新时间：2026-10-31
- 当前状态：**v2 重建计划已落稿，等待执行会话接手**（启动清单见 TASK.md 第 9 节）；任务文件与原型未提交（P0 首件事=经用户确认提交基线）
- v1→v2 变更：2026-10-31 用户决策——不复用原有 CS 业务组件，对照原型全量重建；v1 增量计划（原 P0–P7）作废，差距核对结论保留于 TASK.md 第 10 节

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 | 更新时间 |
|---|---|---|---|---|---|
| P0 | 脚手架与基线（cs2 命名空间/路由壳/pending 快照） | 已完成 | feat/cs-v2-p0 → PR #147（merge 4b80207） | 详见下方日志 2026-10-31（五） | 2026-10-31 |
| P1 | 会话基础链路（双端对话/WS/时间线） | 已完成 | feat/cs-v2-p1 → PR #151（merge b236a62） | 详见下方日志 2026-10-31（六） | 2026-10-31 |
| P2 | 交易与提案（商城三页+资金闭环+确认卡+取消订单+余额） | 已完成 | feat/cs-v2-p2 → PR #155（merge 99c62ea）+ 修复 #156（2945f5b） | 详见下方日志 2026-10-31（七） | 2026-10-31 |
| P3 | 转人工与坐席运营（接入页/状态/转接/SLA/接管后输入） | 已完成 | feat/cs-v2-p3 → PR #164（merge f600be6，三笔 36f40b9/ba87f00/0fc3626） | 详见下方日志 2026-10-31（九）（十） | 2026-10-31 |
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

### 2026-10-31（六）—— P1 实现完成并合入

- **分支**：feat/cs-v2-p1（自 origin/main@ac68c3d；用户已授权「按任务计划持续实施」，每期走 提交→PR→CI→合并→回填 流程，不再逐期询问）。
- **用户端** /customer-service-v2：客服整页（会话下拉/欢迎态/四类气泡/满意度卡/右栏客户资料与最近订单）；SSE 全事件消费（复用 /api/chat/customer-service/stream 的续播+中止契约）；live 时间线 + 打字机逐字 + 停止按钮；纯函数归约器 chatReducer.ts。
- **坐席端** /cs2/workspace：三栏工作台（双 Tab/认领/消息流/右栏会话信息+Trace 入口）；cs2 自有 WS 封装 useAgentSocket（ticket 鉴权、退避重连换新 ticket、重连对账 = 队列全量 + 选中会话按 seq 游标补发 /{id}/events、降级轮询 5s）；回复/认领/关闭走既有 API。后端零改动。
- **测试证据**：定向 frontend 22 + frontend-cs 15 全绿（SSE 全事件断言、WS 生命周期 4 例、工作台交互 4 例、no-legacy 守卫）；全量 T0：frontend 84 files/688 passed、frontend-cs 40 files/297 passed；tsc 无新增错误（frontend 存量 16 例同 P0）；双端生产构建通过。
- **重建与冒烟**：frontend 容器重建 healthy，/customer-service-v2、/mall、/my-orders、/customer-service、/ 均 200；frontend-cs 临时生产起服（错位端口 3399）/cs2/workspace 与旧 /cs/handoff 均 200，验证后已停。
- **交付记录**：commit 7ec63d6 → PR #151 CI 全绿 → merge b236a62。
- **实施中发现并修复**：①WorkbenchPage 挂载漏拉队列（测试抓出）；②选中会话竞态——selectedRef 原在渲染时同步，fetch 回调可能先于 React 提交执行导致守卫误丢响应，改为选择时同步置位；③P1 期间发现「会话已由其他坐席认领」等并发语义依赖既有 409，UI 下轮队列刷新自然收敛。
- **遗留**：转人工四态横幅+倒计时（P3）；接管后用户输入直达坐席（P3）；确认卡/候选点选完整交互（P2，clarification 事件已可消费并有基础点选）；客户360（P2）与 pending 快照消费（P0 API 已就绪）。

### 2026-10-31（七）—— P2 实现完成并合入（含一次 CI 失误的修复）

- **分支**：feat/cs-v2-p2（自 origin/main@a84bf7a）。
- **后端**：Mock 账本 v1.1（钱包 Decimal 记账/充值/下单服务端计价/支付扣款/取消回款，种子六状态）；迁移 125（finance.wallet_*，登记 init_db.py）；MallService 双模式（商品只读 DISTINCT ON 去重/钱包/订单/物流派生）；取消订单全链路（t_order_cancel→提案→确认流 order_cancel 分支：重查事实+幂等退款）；余额意图 t_balance_query + customer-context 扩 balance + 坐席侧会话客户上下文端点；评测样本 C2/C3 各 +2。
- **前端**：mall 命名空间（站点导航/商品中心/购物车/我的订单/余额充值流水）；客服页确认卡（倒计时/改原因 CAS/幂等确认）+ 折叠卡 + 余额；坐席端提案卡 + 客户360。
- **测试证据**：后端定向 10/10（Mock 契约 5 + 取消链路 5）；客服域全量回归 1499 passed（2 failed 经 main 基线复跑证实为存量环境问题：本机双 PG 漂移致 DB 依赖用例不稳，CI 新环境绿）；validate_v2 304 PASS；frontend 688 / frontend-cs 297 全绿；tsc 无新增；双端构建通过。
- **资金闭环实测**（重建后 REST 全链路，X-User-Id:99001 + X-API-Key 模拟网关）：商品→下单 ¥398→支付（5000→4602，pay 流水）→已支付直取消 409→未付款直取消→确认链路退款回款（cancel_refund 入账）；账目平衡。
- **实测/CI 抓出并修复 4 项**：①sqlalchemy text() 参数风格误用（%()s→:name，运行时 500）；②inventory 联表放大商品行（DISTINCT ON）；③直取消端点越权取消已支付单（expected_status 收口）；④demo 账号 tenant_id 缺失（按最新 seed UPDATE 2 行）。
- **交付失误记录**：PR #155 在 3 个 check 尚未通过时被我合并（watch 提前退出后未确认全绿即执行 merge）——其中评测集总量锁定量断言（300）未随补样同步为真实失败，跟进 PR #156 修复（300→304，本地 304 条离线 sanity 全过）；另 2 个（Detect RAG changes/RAG Smoke）为 merge 后上下文丢失的 workflow 基础设施问题，#156 的 CI 全绿反证。教训已吸取：merge 前必须显式确认全部 check 结论。
- **交付记录**：92fb3fc（后端）+ d1f6031（前端）→ PR #155（merge 99c62ea）→ 修复 7a0aa35 → PR #156（merge 2945f5b）。
- **环境备注**：迁移基线已推进至 125（并行会话持续追加，后续期次动手前重新核对）；共享栈运行 http 网关模式（BUSINESS_SERVICE_URL=business-mock:8081），mall 资金走 Mock 账本；frontend-cs 仍不在容器栈（验证以 vitest/tsc/build 为准）。

### 2026-10-31（八）—— P3 实现完成，验证中途按用户指令交接（PAUSED）

- **会话事件**：P3 主体实现过程中用户要求在本可交接点停下，交接另一会话续工。以下为完整交接现场。
- **工作树**：`D:\Program Files\workplace\agent-cs-v2`，分支 `feat/cs-v2-p3`（自 origin/main@3918188 建分支，**改动全部未提交**），基点后 main 仅前移一格（d658387，#158 CI 债务，与改动文件无冲突）。
- **未提交改动**（`git status` 口径）：
  - 新增：`backend/sql/migrations/126_cs_agent_status_sla.sql`、`backend/tests/api/test_cs_agent_status_transfer.py`、`backend/tests/customer_service/test_cs_sla_graded.py`、`frontend-cs/src/cs2/workspace/AgentTopBar.tsx`、`frontend-cs/src/cs2/workspace/TransferDialog.tsx`、`frontend-cs/src/cs2/ConversationsAdminPage.tsx`、`frontend-cs/src/cs2/TicketsAdminPage.tsx`
  - 修改：`backend/customer_service/maintenance.py`（SLA warning 扫描+幂等）、`backend/customer_service/realtime.py`（mark_sla_alert_once）、`backend/customer_service/handoff/dispatch/repository.py`（派单过滤 away/offline + find_agent[_for_update]）、`backend/customer_service/handoff/dispatch/reaper.py`（关闭即 breach + sla.breach 事件）、`backend/customer_service/models/agent.py|handoff.py`（status/sla_level 列）、`backend/app/api/routes/cs_agent_offers.py`（状态 GET/PUT、转接 POST、坐席清单 GET）、`backend/app/api/routes/cs_admin.py`（POST /my/{id}/messages）、`scripts/init_db.py`（126 注册）、`frontend-cs/src/cs2/workspace/api.ts|WorkbenchPage.tsx|workbench-page.test.tsx`、`frontend/src/cs2/cs2Api.ts|CustomerServicePage.tsx`
- **P3 后端已实现**：①迁移 126（cs_agents.status 四态 CHECK + handoffs.sla_level，已注册 init_db.py，**已手动应用到本机 5433 库**——测试需要；容器 db-migrate 重跑幂等）；②派单过滤排除 away/offline（busy 仍派单）；③SLA 分级：warning（50% 窗口）由 maintenance 扫描（Redis SETNX 幂等 fail-open），**breach 由 reaper 关闭路径发出**（职责拆分，原因见 TASK.md 回写）；④API：GET/PUT /cs/agents/me/status（+agent.status_changed 瞬态事件）、POST /cs/conversations/{id}/transfer（本人会话 + 目标 online/busy 校验 + 复用 reassign_handoff + transfer_requested 事件落库）、GET /cs/agents（转接目标清单）、POST /cs/conversations/my/{id}/messages（用户→坐席消息：人工态放行、AI/关闭态 409、跨用户 403、message.created 广播与图 turn 同构）。
- **P3 前端已实现**：坐席端（AgentTopBar 状态切换+通知中心、TransferDialog 目标选择+原因必填、WorkbenchPage 集成 offer 接单条/通知消费 sla.*/offer/转接/状态事件、ConversationsAdminPage 会话管理=筛选+详情回放+强制接管/重派/关闭、TicketsAdminPage 工单管理=筛选+受理/解决/关闭流转）；用户端（转人工 4 态横幅+deadline 倒计时+排队位、人工态 3s 轮询拉坐席消息与 handoff_meta、**接管后用户可继续输入直达坐席**——AI 态仍走 SSE 流）。
- **测试证据（均实际执行）**：新 T2 `test_cs_agent_status_transfer.py` 8 例 + `test_cs_sla_graded.py` 2 例 = **12 passed**（真库集成 5433，p3t- 前缀行用后即清；Redis SETNX 幂等闸真实验证）；既有回归（dispatch/offers/pending/idor）**57 passed**；frontend-cs cs2 定向 **15 passed**、frontend cs2+mall 定向 **25 passed**（no-legacy 守卫含 P3 新文件全过）；frontend-cs tsc **0 错**、frontend tsc 仅存量 16 错（bff-proxy/store-chat，新文件 0 错）。
- **未完成（接手工序，按序）**：
  1. 双端全量 vitest（T0 冒烟）+ 双端生产构建；
  2. `COMPOSE_PROJECT_NAME=agent` 重建 app + frontend（迁移 126 经 db-migrate 幂等落库）+ 新旧路由冒烟 + **实测**：状态切换持久、away 坐席不被派单（需两坐席环境）、转接 A→B、SLA warning 横幅（可用 handoffs 行 created_at 回拨 60s 触发）、接管后用户输入→坐席 WS 收到；
  3. 向用户出示 diff+测试证据，确认后提交（建议两笔：后端/前端，双重路径限定）→ PR → CI → 合并 → 回填本表 P3=已完成；
  4. P3 矩阵行核验（顶栏状态/接单开关=accepting 未做 UI（可 P4 补）、通知铃铛、转接弹窗、会话管理、工单管理、用户端 4 态+倒计时+接管后输入）。
- **交接风险提示**：①测试假 hub stub（_quiet_hub fixture）规避了测试进程 closed-loop 噪音——生产路径未验证广播，重建实测覆盖；②SLA warning 依赖 Redis 可用（fail-open 重复告警优于漏报）；③主栈 reaper 与 maintenance 并行竞争超时行——breach 归 reaper 后已无竞态断言；④worktree 的 .env 与主仓库同步过（P2 时拷贝），若主仓库 .env 变过需重新核对；⑤`POST /my/{id}/messages` 的 `Idempotency-Key` 未做（重复提交会重复落库）——可接受（发送按钮 disabled），如需强幂等 P4 补。
- **交接备忘**：接手会话先 `git status` 核对上述清单，然后读 TASK.md 第 5 节 P3 与本日志，按「未完成」顺序续工；每步验证后推进，不得跳过重建实测直接提 PR。

### 2026-10-31（九）—— P3 续工：工序①②完成，实测抓到并修复 SLA warning 事件丢失缺陷

- **续工核对**：worktree 现场 `git status` 与日志（八）一致；fetch 后 origin/main 前移 3 提交（#159/#160/#161，ci-regression-debt），改动文件与本任务零重叠，无需变基。
- **工序① 双端全量 vitest + 生产构建（均实际执行）**：frontend-cs 40 files/297 passed；frontend 84 files/688 passed；后端新 T2 复跑 12 passed；双端 `npm run build` 成功（frontend-cs 产出全部 /cs2/* 路由，frontend 产出 /customer-service-v2、/mall、/my-orders，旧路由并行）。
- **工序② 重建 + 冒烟 + 五项实测（全部通过）**：
  - 重建口径修正：**各后端服务是独立镜像**（agent-cs-dispatcher、agent-maintenance-worker 各自 build），只 build app 不更新 worker——本会话按 P0 口径重建全部 backend 服务镜像 + frontend，8 容器（app/worker×4/cs-dispatcher/beat/frontend）healthy；db-migrate 幂等 rc=0（126 已应用）；worktree .env 与主仓库 diff 一致。
  - 冒烟：app /health 200；openapi.json 反查 4 个新端点全部挂载（GET/PUT /cs/agents/me/status、POST transfer、POST my messages），未认证 401 闸生效；frontend :3100 新旧 6 路由全 200。
  - 实测①状态持久：PUT away → GET → DB 三方一致（网关头 X-User-Id/X-Tenant-Id + X-API-Key 模拟，坐席 offer 端点拒绝 api-key 通道与设计一致）。
  - 实测②away 不派单：A=uitest_user(away)+presence 心跳、B=cs_wire_e2e(online)+心跳 → `list_accepting_agent_ids` 仅返回 B（P3 status 过滤生效），**常驻 dispatcher 自动派 offer 给 B**（agent_offered + conversation.offered 落库）。
  - 实测③转接 A→B：A 发起 transfer → 200 agent_offered + 事件三连（transfer_requested/reassigned/offered）落库 → B accept → human_active（assignment_version 1→2）。
  - 实测④SLA 分级：回拨 created_at 落 60-120s 窗口，beat 扫描触发 warning；reaper 在 total_deadline 关闭发 breach + 自动转留言工单（GD-20261031-6AFA）；sla.warning(seq 2940)/sla.breach(seq 2939) 事件落库 + Redis cs:events 广播帧均验证。
  - 实测⑤接管后输入：human_active 会话用户 POST /my/{id}/messages → 200 落库（sender_type=user，中文无损）+ message.created 事件落库 + Redis 广播帧（seq 2951，含完整消息体，坐席 WS 通道可达）。
- **实测抓到并修复 1 个生产缺陷（P3 新增）**：maintenance-worker（Celery prefork 进程）从未调用 `bind_loop` → `AgentHub._main_loop=None` → `publish()` 静默丢弃 → **SLA warning 事件现网永不落库、永不广播**（DB sla_level 落、通知中心收不到；T2 测试 _quiet_hub stub 恰好遮蔽此路径）。修复：`_db_loop` 暴露 `get_loop()`，`maintenance.scan_handoff_timeouts` 广播前 `bind_loop(后台 loop, start_subscriber=False)`（与 cs_dispatcher.bind_hub_loop 同模式，幂等赋值；广播经 Redis 由 app 进程订阅转推）。修复后实测 warning 事件落库+广播帧齐备；`test_cs_sla_graded` 复跑 2 passed 无回归；T2 全量 12 passed 复确认。
- **实测口径备忘（非缺陷）**：①`close_stale` 以 **updated_at**（非 created_at）为超时基准——裸 SQL 种数据必须同步刷新 updated_at，否则被提前兜底关闭；②cs_wire_e2e 有 18 条 9 月 e2e 残留 `accepted` assignment 挂账，容量谓词判满载致 no_candidate（已置 released 清理）；③手动 `docker exec python` 进程模型与 prefork 子进程不同，诊断以常驻容器日志 + exec 内复刻事务为准。
- **环境恢复**：p3v- 实测数据全清（0 残留）；uitest_user/cs_wire_e2e 恢复原值（status=online/enabled=f/accepting=f）；next-env.d.ts 构建副产物已还原。
- **待办（工序③④）**：向用户出示 diff+证据 → 确认后提交（两笔：后端/前端，双重路径限定）→ PR → CI → 合并 → 回填 P3=已完成；P3 矩阵行逐项核验。

### 2026-10-31（十）—— P3 交付收官：PR #164 合并 main + 矩阵行核验（工序③④完成）

- **交付记录**：用户确认后按双重路径限定三笔提交——36f40b9（后端：迁移 126/模型/dispatch/maintenance/realtime/路由/T2×2 + SLA 广播缺陷修复）、ba87f00（前端：双端 P3 组件与页面 + TASK/PROGRESS 回写）、0fc3626（迁移守卫登记）→ **PR #164 CI 二轮全绿后合并**（merge f600be6）。
- **CI 首轮失败与修复**：Detect RAG changes/RAG Smoke 失败——#159–#161 新增迁移登记守卫要求新迁移归类，迁移 126 创建早于守卫合入未登记。修复：126（客服域字段，非 RAG/费用）登记 `NON_AI_MIGRATION_ENTRIES`（本地 `classify_migration` 验证 non_ai 后提交）。二轮 7 checks 显式逐项确认：6 pass + RAG Smoke Evaluation skipping（非 RAG PR 预期跳过），无 pending/fail 后才执行 merge（吸取 P2 #155 教训）。
- **意外关机续工记录**：交付中途宿主机意外断电重启——两笔已落盘提交零损失（git log 核对 36f40b9/ba87f00 完好、工作区干净）；Docker Desktop 未随开机自启，**主栈当前停机**，待启动后建议按 main@f600be6 重建（下会话或用户处理，未验证项不作声称）；GitHub 直连抖动（fetch 成功→push 失败→恢复），代理未参与。
- **工序④矩阵行核验（代码+测试+事件链路级；浏览器级逐项打勾按 TASK 第 8 节留 Phase 8 终验）**：
  - 顶栏·状态四态切换持久 ✅（实测①：PUT/GET/DB 三方一致）；away/offline 不接新 offer ✅（实测②：accepting 候选过滤 + 常驻 dispatcher 派 offer 给 online 坐席）
  - 顶栏·通知（SLA/转接/offer）✅（实测④事件落库+Redis 广播帧；AgentTopBar 通知中心消费，vitest 覆盖）
  - 转接弹窗 ✅（T2：原因必填 422/目标 away 422/非本人 403/成功路径事件三连；TransferDialog 原因必填+不可转置灰）；拒单/超时回退 = 既有 reassign 语义回退 waiting_human 队列（TASK 第 5 节回写口径）
  - 会话管理 ✅（ConversationsAdminPage：筛选/回放/强制接管/重派/关闭，复用既有 admin API）；工单管理 ✅（TicketsAdminPage：受理/解决/关闭实时联动，复用既有工单流转）
  - 用户端 4 态横幅+倒计时+排队位 ✅（CustomerServicePage + sla/transfer 事件链路实测）；接管后输入直达坐席 ✅（实测⑤：POST /my/{id}/messages 落库+message.created 广播帧；T2 AI 态 409/跨用户 403）
  - **未完成项（有意顺延）**：顶栏接单开关 UI（accepting 列沿用既有，P4 补）；`POST /my/{id}/messages` 幂等键（P4 可补）
- **遗留提醒**：主栈停机待恢复 + 按合并后 main 重建；下一期 P4 自 origin/main@f600be6 起新分支 feat/cs-v2-p4。
