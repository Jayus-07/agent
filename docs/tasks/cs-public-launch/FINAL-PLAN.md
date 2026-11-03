# cs-public-launch 收尾实施计划（供下一会话执行）

> 生成：2026-11-03 收尾会话。上游档案：本目录 TASK.md / PROGRESS.md / SMOKE-MATRIX.md / Q6-FUNDS-DECISION.md / AGENT-WS-GAP-ANALYSIS.md。
> 本会话已完成主链路真实冒烟实测，合并 PR #289（六项投产阻断修复）+ PR #291（网关豁免 Lua 转义），均已部署验证。本计划覆盖**剩余全部已知问题**的解决方法与实施顺序。

## 一、总计划（五批，每批独立 PR，按序执行）

| 批 | 内容 | 量级 | 依赖 |
|---|---|---|---|
| W1 | 余额问答事实丢失定位与修复（用户端核心体验） | 1 PR（先定位后修） | 无 |
| W2 | 坐席端三个数据/口径小修复：队列终态过滤、转人工率口径、坐席绩效归属 | 3 个小 PR（可并行） | 无 |
| W3 | WS 自动派单修复（P0，NEXT_PUBLIC_WS_ORIGIN 方案） | 1 PR + 部署参数 | 无（与 W1/W2 无代码耦合） |
| W4 | 测试数据治理（先出 dry-run 统计，**执行前须用户拍板范围**） | 脚本 + 立项记录 | 用户拍板 |
| W5 | order_cancel 独立开关（Q6 拍板项 2）+ 收尾总验收 + 部署核对清单 | 1 PR + 验收报告 | W1–W3 完成 |

顺序理由：W1 是用户直接报障的体验问题；W2 三个均为小改动且互相独立；W3 涉及构建参数与部署形态验证放中段；W4 需用户拍板不阻塞代码线；W5 收口。

## 二、工作树与 Git 规范

- **复用现有 worktree**：`D:\Program Files\workplace\agent-cs-fix-entry`（本会话建立，两分支已合并，工作树干净）。每批从最新 origin/main 新建分支：
  - W1 → `fix/cs-balance-answer`；W2 → `fix/cs-queue-filter` / `fix/cs-transfer-rate` / `fix/cs-agent-kpi`；W3 → `feat/cs-ws-origin`；W5 → `feat/cs-order-cancel-switch`。
- .env 已在 worktree（本会话拷贝），缺时从主仓库拷贝；连库显式 5433；提交双重路径限定（`git add -A -- <paths>` + `git commit -m "..." -- <paths>`）。
- GitHub 直连间歇不可达：fetch/push/PR 失败时用 `-c http.proxy=http://127.0.0.1:7897` 或 20–25s 退避重试（本会话已实证有效）。
- 合并后主目录 pull 并重建受影响服务；重建避开并行会话的全量 pytest 窗口。

## 三、已知信息（现场快照，2026-11-03 收尾时）

### 环境
- 本机共享栈全部 healthy：app :8000 / frontend :3100 / frontend-cs :3300 / apisix :9080 / business-mock :8081 / postgres 5433。main 最新含 #289/#291。
- apisix 插件为**目录挂载**（`./apisix/plugins` → 容器 custom_plugins），改 lua 后 `docker restart agent-apisix` 即生效，无需重建镜像。
- `.env`（不入库）已生效配置：`CS_BUSINESS_GATEWAY_MODE=http`、`CS_REFUND_REQUEST_MODE=http`（Q6 拍板项 1 落地）、`CS_HANDOFF_TIMEOUT_SECONDS=600`。
- 隔离资源（上会话遗留，勿动）：agent-cs-prod-q3-pg :15433、agent-cs-prod-q1-admin-check :13200。

### 测试账号
- 用户端：`demo_fuzhou / Demo@2026`（user_id=872，租户 default；business-mock 懒种子 6 单 MO-xxxx，钱包当前 ¥6299）。
- 坐席端：`cs_wire_agent / CsWire@2026`（auth id=24，坐席行 agent_id=`cs_wire_e2e`，role=**supervisor**，accepting=t）。
- 注意：所有真实注册用户 tenant_id 均为 `default`（PR #289 已据此移除 8 处 default 拒绝闸，勿回退）。

### 本会话实证的关键机制（修复时直接引用，勿重探）
1. **business-mock 懒种子**：按 user_id 首访自动生成 6 单（订单号 `MO-` + 8 位大写混合段）+ ¥5000 钱包；**无需 CS_DEMO_MODE**。DEMO-1xxx 只存在于旧 sandbox 种子。
2. **确认提案两层结构**：缺订单号时是 `need_info` 提案（`missing_slots=["order_id"]`、`requires_confirmation=false`），后端 pending 快照已投影权威 `confirmable` 字段，前端据此渲染。
3. **need_info 补槽链**：pending_handler 分支顺序 = CANCEL → HANDOFF → confirmation_state 闸 → **槽位答案（正则 `[A-Z]{1,5}[-_]?[A-Z0-9]{3,}`）→ READ_ONLY 放行**（#289 调整过顺序，勿回退）。
4. **网关豁免**：`/api/cs` 闸只放 supervisor/agent；用户侧端点靠 `ROLE_GATE_EXEMPT_PATTERNS`，**Lua pattern 里字面 `-` 必须写 `%-`**（#291）。
5. **WS 现状**：frontend-cs 页面以 `location.host`(:3300) 连 `ticket.ws_path`（`/ws/cs/agent`），Next standalone 容器无此路由 → 恒「连接中断·轮询降级」；网关 `/ws/cs/*` 路由（enable_websocket → app_chat upstream）已就绪但无人走。
6. **质检/知识/看板空数据均为正常**：质检抽样 crontab 06:05 UTC（batch_date=昨日）本会话时点未到；知识助手"近 7 天暂无未解决问题"经查属实（default 租户窗口内零记录，租户隔离正确）。

## 四、各问题解决方法（文件/行级）

### W1 余额问答事实丢失（P1，用户直接报障）
- **现象**：问"我的余额还有多少"，AI 答"暂时查询不到您的余额信息"。已实证：意图识别正确（LLM 兜底 `t_balance_query` conf=1.00）→ QueryExpert 入口日志两遍后无后续 → service 层**完全正常**（容器内直调 `MallService().get_wallet('872','default')` 返回 balance=6299.0、ledger 2 笔）→ 该文案不在任何代码/模板中，为 LLM 生成。
- **定位步骤（下一会话先做）**：
  1. 容器内直调 `backend.customer_service.experts.query.execute_query('我的余额还有多少', {'intent':'t_balance_query'}, state)`（state 参照既有测试构造 user_id='872'、tenant_id='default'）——若返回正确金额文本，则断点在 supervisor/reporter 的**组合层消费**；若返回"查询不到"类文本，则断点在 execute_query 内部（重点查 `_dispatch_query_service` 的调用参数：user_id 是否被 `resolve_user_id`/identity 改写、tenant_id 是否传了非 default 值、`_use_http_gateway` 分支）。
  2. 对比订单查询（同链路成功）与余额查询的差异分支；查 reporter/LLM 组合 prompt 是否注入 `facts`。
- **假设的候选根因**（按概率）：①组合层 prompt 未消费 `_QueryServiceResponse.facts`，LLM 拿不到余额事实自行编造"查询不到"；②execute_query 上游另有 LLM 改写层把结果覆盖；③tenant_id 传参链路在 state→dispatch 间丢失（走 sandbox 本地路径查不到 872 的账本）。定位后按根因最小修复。
- **修复纪律**：不改变未要求的业务行为；失败降级文案保留；修后补 T2 回归（构造 default 租户用户断言回复含"¥"金额与流水 memo；异常路径仍礼貌降级）。

### W2-1 待接入队列混入终态会话（P1）
- 现象：`GET /cs/conversations/handoff/queue` 返回 15-16 条，含大量 closed/超时历史，点击认领 409。
- 修法：定位 queue 查询（grep `handoff/queue`，在 cs_dispatch/cs_admin 路由层），过滤 `handoff_state IN ('waiting_human','agent_offered')`；保留排序（等待时长）。
- 测试：T2 断言终态 handoff 不出现在响应；认领 409 路径保持。

### W2-2 看板转人工率 >100%（P1）
- 现象：近 7 日转人工率 102%。口径缺陷：转人工数 = handoffs 行数（同一会话多次转人工/超时重开重复计），接待量 = 会话数。
- 修法：dashboard 聚合 API（P7 交付的 overview 端点，grep `转人工率`/`transfer_rate`）转人工分子改 `COUNT(DISTINCT conversation_id)`；若产品语义就是"转人工次数/接待量"则 clamp 100% 并在 UI 标注口径——**推荐去重口径**（与"接待"语义对齐）。
- 测试：构造同会话两次转人工的数据断言分子为 1。

### W2-3 坐席绩效接待量恒 0（P1）
- 现象：当日实际认领并关闭 1 会话，绩效表接待 0。
- 根因：conversations 关闭时 `assigned_agent_id` 被清空（P6 已知设计），绩效 join 依赖该字段。
- 修法：绩效取数改 `handoffs.assigned_agent_id`（handoffs 行保留归属，`handoff_state='closed'` + 时间窗过滤，P6 日报已有同口径先例可复用其 SQL 骨架）。
- 测试：关闭会话后该坐席绩效 +1；未参与坐席仍为 0。

### W3 WS 自动派单修复（P0，方案：构建时注入网关 origin）
- 改动点：
  1. `frontend-cs/src/cs2/workspace/useAgentSocket.ts:52-63`：ws URL 由 `${proto}://${location.host}${wsPath}` 改为 `const origin = process.env.NEXT_PUBLIC_WS_ORIGIN || \`${proto}://${location.host}\`; new WebSocket(\`${origin}${wsPath}?ticket=...\`)`。
  2. `frontend-cs/Dockerfile`：`ARG NEXT_PUBLIC_WS_ORIGIN` + `ENV NEXT_PUBLIC_WS_ORIGIN=$NEXT_PUBLIC_WS_ORIGIN`（next build 构建期内联）。
  3. `部署/prod-4c8g/docker-compose.local.yml` frontend-cs 服务：`build.args` 加 `NEXT_PUBLIC_WS_ORIGIN: ${NEXT_PUBLIC_WS_ORIGIN:-ws://127.0.0.1:9080}`；`.env.example` 补注释（本机默认 `ws://127.0.0.1:9080`；ECS 部署时注入对应网关地址）。
- 验证：①构建后浏览器坐席端登录 → 顶栏 WS 状态 connected（不再恒降级）；②端到端：用户端转人工 → 坐席端**实时**收到 offer/待接入更新（不再只靠轮询）；③认领自动弹窗路径与手动认领并存。
- 风险与回退：env 未注入时回退现状（location.host），零破坏；ECS 部署文档（部署/prod-4c8g/README.md）同步一行说明。

### W4 测试数据治理（P2，**先拍板后执行**）
- 现状：conversations 进行中 1110（p3-/p8r-/p7v-/eval_bot/uitest/t1_train/mcp_gov/acc_verify/e2e_cs 等测试前缀残留）；待接入队列混入终态条目（W2-1 修后消失）；工单列表含"会话原文缺失"eval_bot 工单；ai.unanswered_questions 含 p7v- 测试记录。
- 步骤：①写只读 dry-run 脚本（按前缀白名单统计各表待清数量，输出清单不入库）；②向用户出示统计与拟清范围（明确保留 demo_fuzhou 实测数据与否由用户定）；③确认后执行幂等清理（逐表 DELETE + 计数回显），禁 `down -v`、禁触碰 auth.users 主账号。
- 注意：共享库操作人工确认边界（全局 AGENTS.md 人机边界白名单）。

### W5 order_cancel 独立开关 + 收尾
- Q6 拍板项 2（建议选项 B）：新增 `CS_ORDER_CANCEL_MODE`（代码默认 `http` 跟随网关，显式 disabled 时拒绝取消提案写入），位置对齐 `refund_service` 的模式闸风格；`backend/config/customer_service.py` 定义 + confirmation_flow/cancel_order_service 消费 + T2 测试 + 部署 README 同步。**实施前向用户确认一次默认值语义**（Q6 文件标注"选 B 走小 PR"）。
- 收尾总验收见 §六。

## 五、进度

| 项 | 状态 |
|---|---|
| 主链路真实冒烟实测（退款闭环/转人工联动/七视图巡检） | ✅ 本会话完成（SMOKE-MATRIX.md） |
| PR #289 六项修复（入口404/租户闸×8/网关豁免×4/补槽正则与分支序/confirmable/文案对齐） | ✅ 合并+部署验证 |
| PR #291 网关豁免 `-` 元字符转义 | ✅ 合并+部署验证（右栏 200/折叠卡 200） |
| `.env` 退款开关 + 转人工 600s | ✅ 已生效 |
| 坐席账号 supervisor 升级 + 密码重置 | ✅（已告知用户） |
| W1 余额问答事实丢失 | ✅ PR [#295](https://github.com/Jayus-07/agent/pull/295)：根因=FactSet 白名单缺 balance 投影器（composer 零事实，LLM 编造「查询不到」，容器内复现 `build_fact_set=={}`）；修复=balance 投影器+流水 facts+守卫余额金额类别；真机验证回复含真实金额与流水 memo，订单查询零回归；连带把 composer/facts/query_expert 测试补进 rag_smoke 定向清单 |
| W2-1 队列只列可认领状态 | ✅ fix/cs-queue-filter：`list_open` 缺省谓词由 `!=closed` 收敛为 `IN(waiting_human,agent_offered)`（实测 16 条 human_active 残留混入、认领 409）；REST 验证队列返回 0 条残留 |
| W2-2 看板转人工率口径 | ✅ PR [#298](https://github.com/Jayus-07/agent/pull/298)：分子改 count(DISTINCT conversation_id)（推荐去重口径）；真库 T2 比率 0.5 断言；实测 102%→71.21% |
| W2-3 坐席绩效归属 | ✅ fix/cs-agent-kpi：归属唯一事实源改 assignments.accepted（生产认领链路从不写 handoffs.assigned_agent_id——FINAL-PLAN 原假设不成立，REST 实证 cs_wire_e2e 归属在 assignments）；同根因一并修：趋势转人工序列恒 0、self 口径六卡/意图/趋势；绩效 AHT=unassigned_at-assigned_at |
| W3 WS 自动派单（NEXT_PUBLIC_WS_ORIGIN） | ⬜ 待实施 |
| W4 测试数据治理 | ⬜ 待 dry-run（执行前须用户拍板范围） |
| W5 order_cancel 开关 + 收尾总验收 | ⬜ 待实施（默认值语义实施前确认） |
| :3200 共享管理端部署协调 / ECS 镜像核对 | ⬜ 挂账（非本仓代码任务，需用户/其他会话协调） |

## 六、测试与验收

**测试策略**：全部 T2 起步（权限/口径/契约变化）；每项必带回归测试（构造数据断言，不扩 mock 不 skip）；前后端各自 vitest/pytest 定向跑绿 + Docker 生产构建通过后再 PR；CI 逐项核对终态（skipping 项核对路径过滤原因，本仓库 lua/tests-only PR 的 preflight skipping 属预期）。

**总验收矩阵（全绿才算坐席端硬化完成）**：
1. W1：普通用户问余额，回复含真实金额与最近流水；异常路径礼貌降级；订单查询零回归。
2. W2-1：queue 响应仅含可认领状态；手动认领全通。
3. W2-2：同会话多次转人工时比率 ≤100% 且口径正确。
4. W2-3：关会话后坐席绩效正确计数；跨坐席隔离。
5. W3：坐席端 WS connected 恒态；用户转人工后坐席**秒级**收到 offer（浏览器双端实测）；env 缺省回退不劣化。
6. W5：disabled 时取消提案被拒；默认语义与用户确认一致。
7. 全程：/agent、travel、RAG 主链路零回归（app /health + :3100/login + :3300/cs + :9080 冒烟）。
8. 文档同步：customer-service.md（如行为口径变化）、cross-cutting-contracts.md（如有契约变化）、部署 README（WS 参数）。

## 七、风险与停止条件

- 共享栈 app/frontend 重建有秒级中断窗口——重建前确认无并行会话在跑全量 pytest（冻结纪律），并向用户说明。
- W4 涉及共享库删除：**dry-run 统计与执行之间必须有一次用户确认**，不默认清 demo_fuzhou 实测数据。
- W1 若定位出组合层 prompt 结构问题（候选根因①），改动涉及 LLM prompt——按 prompt 治理流程走版本化，不走热改。
- GitHub 抖动按 §二代理/退避处理；任何一步无法安全重建或验证失败，标 BLOCKED/UNVERIFIED 并停下报告，不伪装通过。

## 八、给下一会话的启动清单

1. 读本文件 + SMOKE-MATRIX.md + AGENT-WS-GAP-ANALYSIS.md + PROGRESS.md（2026-11-03（九））。
2. `git -c http.proxy=http://127.0.0.1:7897 fetch origin` 核对 main（≥8003a0e）；核对 worktree agent-cs-fix-entry 干净。
3. docker ps 全量 + 健康 curl（§三环境节）；核对 .env 三项配置在位。
4. 从 W1 开始按批实施；每批：分支→实现→测试→worktree 重建受影响服务→浏览器/REST 验证→PR→CI 逐项核对→合并→主目录 pull 重建→回填本文件进度节。
