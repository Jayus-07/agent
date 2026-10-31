# 任务：客服双端重建（cs-workstation-v2 · v2 重建版，供执行会话接手）

- 任务 ID：`cs-workstation-v2`
- Worktree：`D:\Program Files\workplace\agent-cs-v2`（执行会话必须在本 worktree 工作，跨会话复用）
- 分支策略：每期独立分支 `feat/cs-v2-pN`（从上一期已合并后的 main 创建），独立 PR 合并 main
- 状态：**v2 重建计划已落稿，等待执行会话接手**；任务文件与原型未提交（Phase 0 首件事=经用户确认后提交基线）
- **v2 变更说明（2026-10-31 用户决策）**：改造方式由"增量复用"改为"**对照原型全量重建，不复用原有 CS 业务组件**"；v1 增量计划（原 P0–P7）作废，以本文为准。原 v1 的差距分析与组件级核对结论仍有效（见第 10 节）。
- 原型基线：`prototypes/agent-workstation.html`（坐席端）、`prototypes/user-portal.html`（用户端）——**所有 UI/交互以原型为目标态**，见第 3 节。

---

## 1. 背景与基线证据

### 1.1 目标

把智能客服的用户端（frontend，:3000）与坐席后台（frontend-cs，:3300）按定稿原型全量重建，并补齐后端缺口，使原型中的全部功能真实可用：统一三栏坐席工作台、退款审核闭环、质检中心、数据看板、知识助手、坐席状态/转接/快捷回复/AI Copilot、用户端商城三页与资金闭环、人工接管后用户可继续对话。

### 1.2 基线证据（执行会话免重探，路径相对仓库根）

- 域图/意图：`backend/customer_service/register.py`、`graph_builder.py`、`router/intents.py`（23 意图）
- 坐席 API：`backend/app/api/routes/` 下 `cs_admin.py`（含确认端点 confirm_router、消息/归属校验收口 :354）、`cs_dispatch.py`、`cs_customer.py`、`cs_agent_offers.py`、`cs_ops.py`、`cs_tickets.py`、`cs_agent_ws.py`
- 派单：`backend/customer_service/handoff/dispatch/{service,offers,reaper,presence,agent_busy,outbox,event_relay}.py`；worker `backend/workers/cs_dispatcher.py`
- 状态机：`models/handoff.py`（迁移 078 CHECK 收敛）；SLA：`models/case.py` + `tasks/cs_maintenance_tasks.py`（handoff_timeout_scan）
- 提案/确认：`confirmation_flow.py`（唯一实现）+ `confirmation_store.py` + 迁移 082（版本 CAS）
- 质检现状：`qa_report.py` + `tasks/cs_qa_tasks.py`（仅每日聚合）；工单：`models/ticket.py`（迁移 036）
- 知识：`faq.py`（双轨）、`knowledge/answer_decision.py`（0.85/0.60 门禁）、迁移 076（cs_faq_query_log）
- 事件契约：`realtime.py`（WS + Redis pub/sub `cs:events`，封套 `{type,event_id,seq,ts}`，seq 游标断线补发）+ `models/event.py`
- business-service Mock：`backend/mock/business_service/main.py`（**订单种子仅 paid/shipped/completed 三态 :96-99；退款审核推进仅测试钩子 :203**）+ `contract.md`（仅 orders/logistics/refund-requests）
- 业务库：`agent_business`（product.products 56 行；customer 99001「演示用户（模拟数据）」订单 DEMO-1001/1002/1003/1006/1010/1012，与 `frontend/src/components/cs/constants.ts:53-67` 演示剧本同源）
- 旧前端：用户端组件 `frontend/src/components/cs/*`（9 组件，见第 10 节核对）；坐席端 `frontend-cs/src/app/cs/*` + `components/agent/AgentAssistPanel.tsx`；用户端导航 `frontend/src/components/layout/navConfig.tsx` 仅 AI 助手/旅游规划/选品漏斗（无商城页）

### 1.3 关键缺口（重建必须补齐，证据见第 10 节）

退款审核无 API/UI（仅测试钩子）；取消订单全链路缺失；无 wallet/余额模型；坐席看不到用户待确认提案；用户端无商城页；人工接管后用户输入被暂停（目标态放开）。

---

## 2. 范围与重建边界

### 2.1 坐席端（frontend-cs，按 prototypes/agent-workstation.html）

| 路由 | 页面 | 角色 | 期次 |
|---|---|---|---|
| `/cs2/workspace` → Phase 8 切换为 `/cs/workspace` | 统一三栏坐席工作台 | agent+ | P1（骨架）→ P2/P3/P4（能力） |
| `/cs2/conversations` | 会话管理（主管监控/回放/Trace/重派·接管·关闭） | agent+ | P1/P3 |
| `/cs2/tickets` | 工单管理（受理/转派/升级/解决/关闭） | agent+ | P3 |
| `/cs2/refunds` | 退款审核工作台 | supervisor | P5 |
| `/cs2/qa` | 质检中心（抽检/评分/AI 预填/申诉/日报） | agent（我的）/ supervisor（全部） | P6 |
| `/cs2/dashboard` | 数据看板（实时卡/趋势/意图分布/坐席绩效） | supervisor（全）/ agent（个人） | P7 |
| `/cs2/knowledge` | 知识助手（未解决问题/话术管理/知识检索/能力差距） | agent+ | P7 |
| 顶栏 | 通知中心（SLA/转接）、坐席状态（在线/忙碌/小休/离线）、接单开关、supervisor/agent 视角切换 | — | P1/P3 |

### 2.2 用户端（frontend，按 prototypes/user-portal.html）

| 路由 | 页面 | 期次 |
|---|---|---|
| `/customer-service-v2` → Phase 8 切换为 `/customer-service` | 智能客服整页（左会话流 + 右客户资料/最近订单） | P1（骨架）→ P2/P3（能力） |
| `/mall` | 商品中心（分类/搜索/详情/购物车/立即购买/咨询客服预填） | P2 |
| `/my-orders` | 我的订单（状态 Tab/去支付/取消/物流/退款预填/再次购买） | P2 |
| 顶栏 | 购物车入口（数量徽章 + 结算弹窗） | P2 |

### 2.3 后端新增能力（复用既有领域服务，只新增不改坏）

| 能力 | 内容 | 期次 |
|---|---|---|
| pending 提案只读快照 | 会话级待确认提案查询（坐席可见性） | P0/P2 |
| 商城最小域（Mock 优先） | 商品查询（映射 product.products 只读）、订单/余额账本 Mock（支付=余额扣款、退款=回款）、取消订单接口；生产支付对接另立项 | P2 |
| 取消订单 | 意图 `t_order_cancel` + 取消 Tool（提案-确认-幂等）+ business-service 取消契约 + Mock 种子扩 pending/cancelled/refunded | P2 |
| 余额查询工具 | customer-context 扩 balance 只读 + 意图应答 | P2 |
| 坐席状态 | cs_agents.status + 派单过滤（away/offline 不派）+ `agent.status_changed` 事件 | P3 |
| 坐席间转接 | handoff 状态机扩展 + 转接 API（目标在线/容量/技能组校验，拒单/超时回退原坐席）+ `conversation.transfer_*` 事件 | P3 |
| 用户端接管后可输入 | human_active 下用户消息通道（`POST /my/{id}/messages`）+ 坐席 WS 接收 | P3 |
| SLA 告警升级 | warning/breach 分级事件（幂等）+ 主管通知 + 升级标记 | P3 |
| 快捷回复 | `cs_canned_responses` 迁移 + CRUD（个人/团队两级）+ AI 建议存为话术 | P4 |
| AI Copilot 事件 | 结构化 `assist.*` 事件（意图/情绪/推荐/知识/SOP/跟进建议） | P4 |
| 退款审核 | 审核 API（队列/通过/驳回+意见）+ 审计 + business-service 正式状态推进（替代测试钩子） | P5 |
| 质检 | `cs_qa_scores` + 每日抽样 Celery + 评分/申诉/终审 + AI 预填 | P6 |
| 看板/绩效 | 运营聚合 API + 坐席绩效 API（租户隔离） | P7 |
| 知识闭环 | 未解决问题聚合（cs_faq_query_log+拒答/低置信）+ FAQ 草稿进既有审核流 | P7 |

### 2.4 非目标

渠道接入（widget/微信/邮件/短信）、语音/呼叫中心、主动营销、排班管理、多语言知识管理；**生产级支付对接**（本期资金闭环为 Mock 账本，生产收银台属商城域另立项）。

---

## 3. 原型基线与功能验收矩阵

- 原型文件：`prototypes/agent-workstation.html`、`prototypes/user-portal.html`（静态演示数据；资金闭环为现状超集演示）。
- **约定：各期 UI 与交互以原型为准实现；本矩阵是总验收的逐项清单，Phase 8 逐项打勾后任务方可关闭。**
- 已知目标态 vs 现状差异：①用户端接管后可继续输入（现状暂停，P3 放开）②退款"审核通过→回款"在原型为演示简化，真实走 P5 人工审核，回款到余额依赖商城 Mock 账本 ③坐席端原型部分对话数据为早期示意（#10023 保温杯），实现统一用 agent_business 演示数据底座（customer 99001 · DEMO-1xxx）。

### 3.1 坐席端验收矩阵

| 区块 | 原型交互 | 期次 |
|---|---|---|
| 顶栏·状态 | 在线/忙碌/小休/离线切换并持久；away/offline 不接新 offer | P3 |
| 顶栏·接单开关 | 开/关即时生效（关=不派新单） | P3 |
| 顶栏·通知 | 铃铛未读数；SLA 超时告警、转接请求；点击定位到对应队列/会话 | P3（SLA 部分 P5） |
| 视角切换 | supervisor/agent 切换：agent 隐藏 数据看板+退款审核，质检只见自己任务，申诉/团队话术 Tab 隐藏 | 全期（随各期落地） |
| 工作台·左栏 | 我的会话/待接入双 Tab；认领（队列→我的列表）；意图徽标/等待时长/未读 | P1 |
| 工作台·中栏 | 消息流（用户/机器人/坐席/系统/AI 阶段分隔）；Enter 发送；typing 指示 | P1 |
| 工作台·提案卡 | 用户待确认提案卡（等待确认→执行中→成功（幂等），状态徽标联动） | P2 |
| 输入区·快捷回复 | 面板搜索/标签过滤/点击插入；「存为话术」后可在面板搜到；个人/团队两级 | P4 |
| 输入区·AI 建议 | 采用（填入输入框）；跟进建议流（发送后带「新」标记） | P4 |
| 输入区·转接 | 弹窗：指定坐席（在线/负载/技能展示，小休不可转）或技能组；原因必填；发起后等待回执，拒单/超时回退 | P3 |
| 输入区·更多 | 发送订单卡片 / 内部备注（仅坐席可见）/ 转为工单 / 关闭会话（状态联动+评价邀请） | P3/P4 |
| 右栏·客户360 | 会员/注册/**账户余额（只读）**/近期订单/标签 | P2 |
| 右栏·会话信息 | 意图/状态/SLA、订单上下文、**用户待确认提案**、AI 阶段摘要、Trace 入口 | P1（提案行 P2） |
| 右栏·AI 助手 | 监听态呼吸灯、意图识别+置信、情绪检测、推荐回复（采用/存为话术）、知识推荐（引用回复/存为话术）、订单速览、SOP 下一步 | P4 |
| 会话管理 | 关键词/状态/意图筛选；详情：消息回放/状态时间线/Trace 耗时条；主管重派/强制接管/关闭 | P3 |
| 工单管理 | 筛选；详情处理记录时间线；受理/转派/升级 SLA（×N 累加）/标记解决/关闭 实时改列表 | P3 |
| 退款审核 | 队列+搜索/状态筛选；详情（原因/政策版本/提案幂等/会话上下文）；**意见必填**；通过/驳回；终态不可回退；审计提示 | P5 |
| 质检中心 | 抽检任务（选中加载、AI 预填建议、拖分实时合计、提交改状态）；申诉（维持/改判）；质检日报表 | P6 |
| 数据看板 | 今日/近7日/近30日切换（六卡数值+涨跌联动）；近7日接待/转人工对比；意图分布；坐席绩效表（含质检分/当前状态） | P7 |
| 知识助手 | 未解决问题（点行展开原始查询聚合、生成 FAQ 草稿）；话术管理（个人/团队 Tab、新增弹窗真实加行并同步快捷回复、启用开关）；知识检索（结果+置信）；能力差距清单 | P7 |

### 3.2 用户端验收矩阵

| 区块 | 原型交互 | 期次 |
|---|---|---|
| 站点导航 | 仅 智能客服/商品中心/我的订单 + 购物车徽章 | P2 |
| 客服·会话 | 会话下拉切换（进行中/已结束）；+ 新会话→欢迎态 | P1 |
| 客服·欢迎态 | AI 智能客服徽章、免责语、🧪 演示沙盒标记、快捷入口 4（DEMO- 剧本）、转人工横条（无消息禁用） | P1 |
| 客服·消息流 | 用户绿气泡/机器人带「AI 客服·由人工智能生成」/坐席徽章/系统；打字机逐字 + 发送键变「停止」可中断 | P1 |
| 客服·时间线 | 发送时「本轮处理进度」live 节点逐个点亮，完成后固化在气泡下（客服图执行→调度→检索→汇总） | P1 |
| 客服·确认卡 | 退款/取消两类；masked_target 脱敏；**有效期倒计时**，过期置灰+提示；修改原因（版本 v2）；确认/取消回执气泡 | P2 |
| 客服·候选点选 | 「点选要办理的订单（也可直接回复序号）」→ 点选后生成提案 | P2 |
| 客服·转人工 | 4 态横幅（requested/waiting/active/closed）+ 等待倒计时「剩余 mm:ss · 前面 N 位」；人工接入后坐席消息（轮询/WS） | P3 |
| 客服·接管后输入 | **人工服务中用户可继续输入，消息直达坐席**（目标态变更，现状为暂停） | P3 |
| 客服·满意度 | 有回复且未评分时出现；星级+选填备注+暂不评价；提交后绿色致谢 | P1（数据 P2 联动） |
| 客服·折叠卡 | 我的退款申请（状态徽章 + 「编号不代表已退款」→ 审核联动）；我的工单（空态折叠卡） | P2/P5 |
| 客服·资金应答 | 「余额/到账」查询回复余额+最近流水 | P2 |
| 商品中心 | 分类 chips+搜索；商品卡（品牌/SKU/价格/库存）；详情（加购/立即购买/咨询客服预填）；购物车弹窗（移除/清空/合计/结算生成待付款订单） | P2 |
| 我的订单 | 状态 Tab（含已退款）；**去支付（余额校验+扣款+流水）**；未付款直接取消；待发货取消走客服提案；物流轨迹弹窗（not_shipped 区分）；申请退款/咨询此订单预填；退款后状态流转 | P2 |
| 资金闭环 | 余额 ¥5,000 起 + 充值 + 流水（支付 −/退款 +/充值 +）；余额不足拦截 | P2 |

---

## 4. 总计划（重建分期）

| 期 | 主题 | 后端 | 前端（全新组件树） | 依赖 |
|---|---|---|---|---|
| P0 | 脚手架与基线 | pending 提案只读快照 API；迁移编号基线核对（实况：`backend/sql/migrations/` 最新 099 且存在双 082，新迁移自 **100+** 顺延；2026-10-31 P0 执行会话核对回写） | `frontend-cs/src/cs2/`、`frontend/src/cs2/`、`frontend/src/mall/` 命名空间与路由挂载（/cs2/*、/customer-service-v2、/mall、/my-orders）；双端健康检查 | 无 |
| P1 | 会话基础链路 | 事件/消息/SSE 契约复用（不改） | 坐席端三栏工作台骨架（列表/聊天/上下文+状态栏/输入）；用户端客服整页（欢迎态/消息流/打字机/停止/时间线/满意度卡） | P0 |
| P2 | 交易与提案 | 商城 Mock 账本+商品映射；取消订单全链路；余额查询工具；pending 快照 | 用户端商城三页+购物车+资金闭环；确认卡（两类）/候选点选/退款·取消折叠卡；坐席端提案卡+客户360（含余额）+pending 可见性 | P1 |
| P3 | 转人工与坐席运营 | 坐席状态+派单过滤+事件；转接状态机+事件；用户→坐席消息通道；SLA 分级事件+升级 | 坐席端接入页（队列/认领/回复/关闭/重派）+转接弹窗+状态切换+通知中心+会话管理详情；用户端转人工 4 态+倒计时+接管后输入 | P1（P2 建议） |
| P4 | 快捷回复+AI Copilot | canned CRUD；assist.* 结构化事件；存为话术 | 坐席端快捷回复面板+AI 助手面板（五块）+更多菜单 | P1 |
| P5 | 退款审核闭环 | 审核 API+审计+business-service 正式状态推进 | 坐席端 /cs2/refunds；用户端折叠卡状态联动+站内通知 | P2（提案链路） |
| P6 | 质检中心 | cs_qa_scores+抽样+评分/申诉/终审+AI 预填 | 坐席端 /cs2/qa | P0 |
| P7 | 看板+知识助手 | 聚合/绩效 API；未解决聚合+FAQ 草稿 | 坐席端 /cs2/dashboard、/cs2/knowledge | P3（状态数据） |
| P8 | 切换与清理 | 无 | 路由切换（/cs2→/cs、/customer-service-v2→/customer-service）；删除旧 CS 组件/页面；文档同步；原型矩阵终验 | 全部 |

节奏：P0 → P1 → P2 → P3 →（P4/P5/P6 可并行）→ P7 → P8；每期独立分支+PR，合入后更新 PROGRESS.md。**任何一期合入前：旧路径冒烟（旧 handoff 页/旧客服页）+ 新页面验收，双绿才合**（防止主线断裂）。

---

## 5. 分期实施详情

### P0 脚手架与基线

- 后端：pending 快照只读端点（会话级，supervisor/所属坐席可见）；核对迁移编号（实况 099 最新、双 082，新迁移 100+ 顺延，2026-10-31 核对）。
- 前端：三处新命名空间建目录与路由壳（空页+健康自检）；**禁止 import 旧 CS 组件**（Phase 8 用 grep 断言验收）；顶栏/侧栏新壳（frontend-cs 的 cs2 布局独立于旧 Sidebar）。
- 测试：T1 新壳路由可达+健康；T0 旧全量不受影响（旧页面照常打开）。
- 验收：双端新路由可达、旧功能零回归、grep 无旧组件引用。

### P1 会话基础链路

- 后端：无新增（复用 SSE/WS/事件契约；只读）。
- 坐席端 cs2：三栏工作台（会话列表双 Tab 数据接既有 queue/my API；聊天窗接 WS+补发+降级轮询；右栏会话信息+状态栏+输入）；通知/状态占位。
- 用户端 cs2：客服整页接既有 SSE（meta/status/delta/done/error/paused/clarification 全事件消费）；欢迎态/打字机/停止/时间线/满意度卡。
- 测试：T2 SSE 事件全类型断言、WS 断线重连+补发+降级轮询、双端同会话消息一致；T3 手工双端对话。
- 验收：矩阵 P1 行全过；零旧组件引用。

### P2 交易与提案

- 后端：商城 Mock 账本（余额/流水/订单状态机 pending→paid→shipped→completed/cancelled/refunded；支付=扣款、退款审核通过=回款）；商品只读映射；取消契约+意图+Tool；余额查询工具；customer-context 扩 balance。Mock 种子扩至五态（连带更新 contract.md 与测试）。
- 用户端：商城三页+购物车；确认卡（退款/取消、有效期、修改原因）；候选点选卡；退款/取消折叠卡；余额应答。
- 坐席端：提案卡（等待/执行中/成功）、客户360（余额）、pending 可见性。
- 测试：T2 提案状态机/CAS/幂等/过期/越权（取消他人订单拒绝）、支付扣款与余额不足、取消与派单竞态；评测集补取消/余额样本。
- 验收：资金闭环「买→付→取消/退款→回款」全通；两端数据一致。

### P3 转人工与坐席运营

> **2026-10-31 执行回写（以仓库现状为准）**：①转接实现复用 `offers.reassign_handoff`，目标**拒单/offer 超时回退 `waiting_human` 队列**（原坐席可在队列再次认领），非本节原拟的「回退原坐席」——定向回退需侵入 decline 路径，成本高于收益，如需再立项；②SLA 分级职责拆分：**warning（50% 窗口）由 `maintenance.scan_handoff_timeouts` 扫描发出（Redis SETNX 幂等）**，**breach 由 reaper 关闭路径发出**（TOTAL_DEADLINE 关闭即置 sla_level=breach + sla.breach 事件）——dispatcher 每秒扫描必先于 beat 扫描关闭超时工单，maintenance 发 breach 在现网是死代码；③坐席状态落地为 `cs_agents.status`（126 迁移，online/busy/away/offline），派单过滤排除 away/offline；「接单开关」沿用既有 `accepting` 列（顶栏 UI 可 P4 补）；④用户→坐席消息通道 = `POST /cs/conversations/my/{id}/messages`（人工态放行，广播 message.created 与图 turn 同构）。

- 后端：cs_agents.status+过滤+事件；转接状态机（human_active→transfer_requested→复用 offer→回退）+迁移；用户→坐席消息通道；SLA warning/breach（幂等，CS_HANDOFF_TIMEOUT 联动）+升级标记。
- 坐席端：人工接入页（队列/认领/offer/回复/关闭/主管重派、WS 重连降级）；转接弹窗；状态切换；通知中心；会话管理详情（回放/时间线/Trace/重派/接管/关闭）；工单管理。
- 用户端：转人工 4 态+倒计时；接管后输入直达坐席。
- 测试：T2 派单过滤、转接四竞态（转接/认领/重派/超时回收）、SLA 幂等、用户→坐席消息归属校验；T3 A→B 转接+拒单回退。
- 验收：矩阵 P3 行全过。

### P4 快捷回复+AI Copilot

> **2026-10-31 执行回写（以仓库现状为准）**：①`assist.suggestion` 瞬态事件链路为既有基建（批次A：knowledge/snippet/order 三路），P4 增量为面板字段——`intent`（取最近一条带意图消息，中文映射 FINE_INTENTS+置信）、`emotion`（关键词规则 alert/warn/ok，alert 追加情绪预警卡）、`sop`（24 意图静态模板+默认，可配置化留 P7+）、触发矩阵扩 `human_agent→followup`；②话术表迁移 127（personal/team 两级 CHECK），团队写仅 supervisor；③「更多菜单」（订单卡片/内部备注/转为工单）**顺延 P5+**——三者无后端能力支撑（messages 无 internal 类型、工单无创建 API、无卡片消息体），不做假按钮，Phase 8 复核该矩阵行口径；④瞬态 assist 事件在无坐席 WS 连接时不广播（`_persist_and_broadcast` 的 `_connections` 短路，合理设计）——实测须先建立 WS 连接。

- 后端：`cs_canned_responses`（个人/团队，迁移）；CRUD 权限（个人读写、团队读全员写 supervisor）；`assist.*` 结构化事件（意图/情绪/推荐/知识/SOP/跟进）。
- 坐席端：快捷回复面板（搜索/插入）；AI 助手面板五块；更多菜单；存为话术闭环（AI 建议→个人话术→面板可搜）。
- 测试：T2 话术权限矩阵（个人/团队/跨租户）+ assist 事件契约（只追加）；T1 面板交互。
- 验收：矩阵 P4 行全过。

### P5 退款审核闭环

- 后端：审核 API（队列/通过/驳回+意见必填）+ 审计（agent_actions/audit_logs）；business-service 正式状态推进（approved/rejected），替代测试钩子（钩子保留仅供测试，加注）。
- 坐席端：/cs2/refunds（队列/详情/意见/通过/驳回/终态锁定）。
- 用户端：折叠卡状态联动（等待人工审核→已退款/已驳回）+ 站内通知。
- 测试：T2 审核权限（仅 supervisor）/状态机不可回退/审计断言/与幂等交互；T3 提交→通过 与 提交→驳回 双路径。
- 验收：审核在 UI 闭环；用户可见状态变化。

### P6 质检中心

- 后端：cs_qa_scores（迁移）+每日抽样 Celery（参数化 N）+评分/申诉/终审 API+AI 预填（source=ai，人工终审）。
- 坐席端：/cs2/qa（任务/评分/AI 预填/申诉/日报打通）。
- 测试：T2 抽样去重/评分申诉状态机/权限（agent 仅本人）/租户隔离。
- 验收：抽检→评分→申诉→终审全流程；日报含人工抽检数据。

### P7 看板+知识助手

- 后端：运营聚合+坐席绩效 API（首响/AHT/CSAT/质检分/状态）；未解决问题聚合+FAQ 草稿进既有管理端审核流；涉知识链路同步 `.github/workflows/rag_smoke.yml`。
- 坐席端：/cs2/dashboard（时间范围切换联动六卡/趋势/意图分布/绩效表）；/cs2/knowledge（未解决/话术/检索/能力差距）。
- 测试：T2 聚合正确性（构造数据比对）+租户隔离+agent 仅本人绩效。
- 验收：看板数值与 DB 抽查一致；拒答问题→FAQ 草稿→管理端审核可走通。

### P8 切换与清理

- 路由切换：frontend-cs `/cs/*` 指向 cs2 页面并删除旧页；frontend `/customer-service` 指向新页并删除旧 `components/cs/*` 与旧抽屉。
- 删除前建「旧契约→替代→覆盖证据」映射（testing-guide 口径）；清理未接组件（如旧 UploadDialog）。
- 连带：`docs/domains/customer-service.md`、`frontend-guide.md`、`cross-cutting-contracts.md`（新事件）、`commands.md` 必要更新；评测集/rag_smoke 清单核对。
- 验收：原型矩阵 3.1/3.2 逐项打勾；`grep` 断言新命名空间零旧组件引用；定向回归全绿；双端主链路（对话/退款/转人工/审核）实测通过。

---

## 6. 技术约定（执行会话必须遵守）

- **不复用边界**：新建组件树 `frontend-cs/src/cs2/**`、`frontend/src/cs2/**`、`frontend/src/mall/**` 禁止 import 旧 CS 业务组件与 hooks（`frontend/src/components/cs/*`、frontend-cs 旧 `app/cs/*` 页面组件、旧 useCSChat/useCSHandoffSync）；**允许复用与业务无关的基建**：`lib/auth.ts`、API client、Tailwind 配置、lucide、react-query/zustand 安装。后端领域服务（confirmation_flow/dispatch/graph/tickets）原样复用——"重建"不含推倒后端。
- 路由过渡：新页挂 `/cs2/*`、`/customer-service-v2`；商城 `/mall`、`/my-orders` 直接用最终路由；Phase 8 统一切换。
- 事件契约只追加；新迁移 100+ 顺延（实况基线 099，2026-10-31 核对）；状态机变更走 CHECK 收敛新迁移；金额一律 Mock 账本 DECIMAL 语义，禁止浮点累加展示。
- 每期代码完成后用本 worktree 代码 `devctl.bat rebuild` 重建受影响服务并确认健康；worktree 缺 .env 从主仓库拷贝；并行验证用错位端口；连库显式 5433。
- 提交纪律：`git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定；提交前核对引用符号已落库；不与并行会话抢容器/端口（先 docker ps 查归属）。
- 测试从仓库根：`python -m pytest backend/tests/<路径> -q --no-cov`；前端各 app `npm test -- <文件>` + `npx tsc --noEmit`。

---

## 7. 测试策略

- 遵循 `docs/development/testing-guide.md`：文档/契约 T0；单模块 T1；**本任务涉权限/状态机/公共契约，默认 T2 起步**；T3 仅 Phase 8 总验收与用户要求。
- 每个新 API 必带权限与租户隔离测试；事件类改动断言封套/seq 不变、只增不破。
- 双跑冒烟：P8 切换前，旧路径与新路径每期合入时各冒烟一次。
- AI 质量：修改路由/知识/意图时按 `docs/evaluation/evaluation-governance.md` 选对应 Suite；评测集补取消订单/余额查询样本（P2）。
- 交付记录：命令、PASS/FAIL/SKIP、耗时、未运行范围；未实际执行不得报通过。

## 8. 总验收标准

1. 第 3 节矩阵逐项打勾（Phase 8 终验，附截图/响应证据）。
2. 新命名空间零旧组件引用（grep 断言入 PR 检查）。
3. backend/tests/customer_service 与 frontend 三 app vitest 定向全绿；每期 PR CI 绿。
4. 资金闭环实测：买→付→取消→退款→回款，两端数据与流水一致。
5. 审核闭环实测：提交→supervisor 通过/驳回→用户侧联动+审计记录。
6. 文档同步：customer-service.md / frontend-guide.md / cross-cutting-contracts.md / commands.md。
7. 旧 CS 组件/页面已删除，路由已切换，主链路（/agent、travel、RAG）零回归。

## 9. 会话交接（执行会话启动清单）

1. `cd "D:\Program Files\workplace\agent-cs-v2"`；`git status` + `git log` 核对工作区与最近提交；发现他人改动先报告。
2. 通读本文 + `PROGRESS.md` + 两份原型（浏览器打开 prototypes/*.html 对照）。
3. 从 PROGRESS 阶段表中最早的「计划中」期开始；每期：建分支 `feat/cs-v2-pN`（自最新 main）→ 实现 → 测试 → devctl 重建健康 → 向用户出示 diff+测试证据 → 确认后 PR 合并 → 回填 PROGRESS。
4. 遇与原型/本文冲突的事实（代码、契约、迁移），以仓库现状为准并回写本文，不擅自改原型。
5. 暂停时按仓库规范记录 PAUSED 现场（HEAD/未提交/下一步）。

## 10. 差距核对结论（v1 遗留，仍有效，2026-10-31）

10 类场景组件级核对（✅/🟡/❌）：查询类/投诉/幂等过期 ✅；退款 🟡（审核闭环缺失、坐席不可见 pending）；已发货退款 🟡（退货寄回无契约）；取消订单 ❌；余额 ❌；支付/商城页 ❌；商品咨询 🟡。关键证据：frontend-cs 无 PendingAction 消费；审核推进仅 `backend/mock/business_service/main.py:203` 测试钩子；无 wallet；intents 无 cancel；Mock 种子仅三态；用户端 navConfig 无商城页。

## 11. 后续候选池（P9+，未排期）

价保退差价（部分退款）、催发货（意图级）、账户注销（合规红线）、生产支付对接（商城域）、多语言、渠道接入与语音（用户指定不动）。

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 重建期间主线断裂 | 新旧并行+每期双跑冒烟；Phase 8 才切路由删旧 |
| 重建范围蔓延到后端推倒 | 第 6 节边界：后端只增不改坏；领域服务原样复用 |
| 原型交互遗漏 | 第 3 节矩阵即契约，Phase 8 逐项验收 |
| 转接/取消与既有并发机制竞态 | P2/P3 专项并发测试矩阵；CHECK 约束收敛 |
| 资金 Mock 与真实业务脱节 | 账本建模对齐 DECIMAL/幂等键；生产支付另立项（第 2.4 节） |
| 多会话并行干扰 | 独立 worktree+每期分支+路径限定提交；动容器先查归属；全量 pytest 冻结纪律 |
