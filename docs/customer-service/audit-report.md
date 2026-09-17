# 企业级智能客服系统审查报告（audit-report.md）

> 依据：`docs/customer-service/REFACTOR-TASK-SPEC.md` 第一阶段要求
> 审查时间：2026-09-17 20:15 ~ 21:00（只读审查，未改任何代码）
> 审查范围：backend/customer_service 全部 70 文件（8854 行）、cs_admin/cs_agent_ws/chat 路由、APISIX 配置、frontend 与 frontend-admin 客服链路、backend/tasks、迁移 SQL
> 证据规范：所有结论均带 `文件:行号`；未经实测确认的机制标注「待核实」

---

## 1. 当前真实架构图和请求链路

### 1.1 真实拓扑

```
用户端 frontend/ (/agent 页 CSDrawer)
   │  POST /api/chat/stream (fetch 流式, 非原生 EventSource)
   │  GET  /cs/conversations/{id}/messages?since_id   (2s 轮询收人工消息)
   ▼
APISIX (gateway-auth.lua: JWT 验签/头注入/限流 600/min; chat-sse 独立路由 600s)
   │  ⚠ /ws/* 无任何路由 —— WebSocket 不走网关
   ▼
FastAPI backend/app/api/routes/
   ├─ chat.py:108 POST /chat/stream → 主图 GraphRunner → router_node 内 cs_prefilter
   └─ cs_admin.py (prefix /cs/conversations) + cs_agent_ws.py (WS /ws/cs/agent?ticket=)
   ▼
主图 router_node 内 L0 cs_prefilter (orchestration/graph/cs_prefilter.py)
   │  转人工直通(正则) → 人工接管期强制接管 → 域检测(embedding) → 灰度
   │  → 产出 CSRouteResult 存入 cs_context.cs_route
   ▼
CS 子图 (9 节点, graph_builder.py:89-109):
   START → cs_state_loader → cs_pending_handler ─(无 pending)→ cs_supervisor
         → cs_{knowledge|query|action|complaint|handoff}_expert
         → cs_supervisor (可循环, 护栏: max_loops=5, recursion_limit=20)
         → cs_reporter → END
   (有 pending: 过期/确认/取消/追问 → 直接 cs_reporter)
   ▼
存储: PostgreSQL (conversations/messages/handoffs/confirmations/cs_agents/assignments/customers)
      内存 dict Store (HandoffStore/ConfirmationStore L1) + 进程内 WebSocket Hub
      LangGraph Checkpointer: PostgresSaver(降级 MemorySaver)
```

### 1.2 管理端链路

```
frontend-admin /cs/handoff (坐席工作台)
   │  Bearer JWT → Next BFF (app/api/[...path]/route.ts:44-52 注入 X-API-Key)
   │  WS 主通道 ws://127.0.0.1:8000/ws/cs/agent (硬编码绕过网关, 断线退避重连)
   │  WS 失败 → 2s 轮询降级 (handoff/page.tsx:316-332)
   ▼
cs_admin.py: queue / claim / agent-messages / close / messages?since_id / stats
```

---

## 2. 所有客服入口及 API 调用关系

### 2.1 用户侧入口

| 入口 | 位置 | 链路 |
|---|---|---|
| `/agent` 页客服抽屉 | frontend/src/app/agent/page.tsx:10,70 → CSDrawer.tsx | 发消息→/chat/stream(SSE)；历史→GET /cs/conversations/my；人工消息→2s 轮询 messages；评分→POST /{id}/rating |
| 主图聊天 | POST /chat/stream | chat.py:108-287，事件协议 meta/status/log/delta/thinking/done/error |

### 2.2 管理端 API（cs_admin.py，全部仅 X-API-Key）

GET /cs/conversations（列表 :127）· GET /stats（:166）· POST /{id}/rating（:177）· GET /my（:233，**唯一有用户归属校验**）· GET /{id}（:260）· GET /{id}/traces（:285）· GET /handoff/queue（:349）· POST /agent/ws-ticket（:368）· POST /{id}/close（:388）· POST /{id}/claim（:483）· POST /{id}/agent-messages（:509）· GET /{id}/messages?since_id（:527）

### 2.3 关键事实

- **没有独立 `/cs/confirm` HTTP 端点**：确认动作靠图内 pending_handler 文本关键词识别（confirmation.py:97-122），不走 API。
- CS 子图注册走 domain_graph_registry（register.py:10-15），不是 API 路由注册。

---

## 3. 所有状态来源和状态转换关系

| 状态 | 权威数据 | 实际实现 | 问题 |
|---|---|---|---|
| 会话/消息 | conversations/messages 表 (006_customer_service.sql) | conversation_store.py | fire-and-forget 吞异常（:48-49,213-216）；历史上 role 列缺失曾致全部消息静默丢失（models/message.py:46-49 注释自证） |
| 转人工 | handoffs 表 (Alembic 0004) | HandoffStore 内存 dict→DB 旁路 | DB 写失败静默降级 "cache-only mode"（handoff_store.py:95-103）→ 内存与 DB 永久分叉 |
| 确认动作 | confirmations 表 (006:168) | ConfirmationStore 同上 | 同上（confirmation_store.py:64-71） |
| 坐席/分派 | cs_agents/assignments/customers 表 | cs_admin.py | claim 无锁无原子条件更新（:868-933） |
| 审计 | agent_actions/audit_logs 表 | **表已建，全库零写入**（006:62,104；grep 无 INSERT） | 高风险操作无持久审计 |
| 图状态 | PostgresSaver checkpoint | graph_builder.py:136-181 | 失败降级 MemorySaver；thread_id=conversation_id（cs_graph_node.py:127-139） |
| Redis | **客服域零使用**（grep 无匹配） | — | 事件、锁、去重全部缺位 |

### 3.1 状态机实现

- **转人工**（handoff.py:29-47）：`AI_ACTIVE→HANDOFF_REQUESTED→WAITING_HUMAN→HUMAN_ACTIVE→CLOSED`，含 HANDOFF_REQUESTED→AI_ACTIVE 回退与 CLOSED 吸收态——与任务书一致。但 HandoffExpert 同函数连续两次 store.save（experts/handoff.py:119-130），DB 暴露中间态。
- **业务动作**（confirmation.py:33-45）：`PENDING→CONFIRMED|CANCELLED|EXPIRED`、`CONFIRMED→EXECUTING→SUCCEEDED/FAILED`；无独立 PROPOSED 态（proposal 即落 pending）。
- **超时**：确认过期响应式检查（pending_handler.py:69）；转人工超时**纯响应式**——只在下一条用户消息时检查（supervisor.py:372），无人说话则工单永久悬挂。无任何 Celery 定时扫描。

---

## 4. LangGraph 节点、边、循环和 checkpoint 关系

- 9 节点：cs_state_loader / cs_pending_handler / cs_supervisor / cs_reporter + 5 expert（graph_builder.py:89-98）。
- 边：静态 5 条（:100-109，所有 expert 固定回 supervisor）+ Command 动态路由（pending_handler.py:35/104/150/226/256/305；supervisor.py:401-409）。无 conditional_edges。
- **无 interrupt()**：多轮确认不是 LangGraph interrupt，而是自制机制——每 turn 重进图 + pending_action 持久化（state_transition.py:372-376）。
- 循环护栏三层：expert_loop_count≥5 强制 finish（supervisor.py:148-155）、同一 expert 连续 2 次强制 finish（:169-176）、recursion_limit=20（cs_graph_node.py:132-135）→ 无无限循环路径。
- 状态快照：loader 从 DB 经 StateTransitionService.load_snapshot 加载（graph_builder.py:40-74 → state_transition.py:329-344）；加载失败返回默认快照（:342-344）→ pending_action 丢失，确认请求静默落入 ActionExpert 旧重复实现。

---

## 5. Celery、Redis、PostgreSQL 的实际使用情况

- **Celery：客服域零接入**。backend/tasks/ 只有 agent_tasks（主图）与 index_tasks（RAG 索引）。转人工超时扫描、确认过期清理、事件补偿等任务全部不存在；通用基建（tasks 表持久化 celery_task_id/retry_count、指数退避、signals 埋点）已齐备（tasks/schema.sql:23,27；config/tasks.py:24-28）但客服域未用。
- **Redis：客服域零使用**。仅承担 Celery broker/backend（config/tasks.py:15-16）与 RAG 进度镜像。realtime.py 是纯进程内 WebSocket Hub，不用 Redis pub/sub → 多进程部署时事件只能达本进程坐席；无序列号/event_id/去重/补发，publish 失败仅 debug 日志（realtime.py:92-113）。
- **PostgreSQL**：查询侧真实直查（order_service.py:87-122、account_service.py:43-48、logistics_service.py:80-88 等）；写侧全部 simulate（refund_service.py:102-121 等，status="simulated"，文案与审计均明示，**不伪装真实成功**）。但 `_has_existing_refund` 查询失败返回 False（refund_service.py:160-163）→ 降级方向不安全，放行重复退款。

---

## 6. 前后端功能断链清单

| # | 断链 | 证据 | 级别 |
|---|---|---|---|
| 1 | **CSConfirmCard 完全未接通（死代码）**：组件存在但全仓无 import；setConfirmationState 无调用方；SSE done 帧不下发 pending_action 结构。确认退化为文本关键词匹配 | frontend/src/components/cs/CSConfirmCard.tsx:11；store/csChat.ts:281；chat.py done 帧 | **P0** |
| 2 | **WebSocket 不走网关**：APISIX 无 /ws/* 路由；管理端硬编码 ws://127.0.0.1:8000。本地能跑，容器化/跨机部署实时推送全断 | apisix.yaml 全文；csAgentWs.ts:19 | **P0** |
| 3 | **客服读写端点 IDOR**：messages/{id}/rating/agent-messages 等只验 API-Key 不验 conversation.user_id 归属；conversation_id=可预测 nanoid；坐席 agent_id 是客户端自由声明字符串，无坐席身份体系 | cs_admin.py:127/177/326-327/509/527；对比 /my 有校验 :247-249 | **P0** |
| 4 | 用户侧人工消息轮询落在管理 API 前缀（/cs/conversations/{id}/messages），用户/坐席共端加剧 IDOR | useCSHandoffSync.ts:55 | P1 |
| 5 | 用户端 SSE 无断线重连（fetch 流中断即终止）；轮询无退避 | useCSChat.ts:57-72 | P1 |
| 6 | 错误协议三套并存：网关 {"error","detail"}（gateway-auth.lua:19）、FastAPI {"detail"}、SSE event:error（chat.py:72-76）；CS 异常树（errors.py）未接全局 handler | — | P2 |
| 7 | 管理端列表/详情/统计无管理员权限闸（require_admin_user 已有未用，deps.py:313） | cs_admin.py:127/166/260 | P2 |

---

## 7. 无法运行、异常吞掉、重复实现和废弃代码清单

### 异常吞掉（违反任务书第 9 节禁令）

- `except: pass`：experts/query.py:209-210、experts/complaint.py:52-53、cs_prefilter.py:131-132
- DB 写失败降级 cache-only 静默：handoff_store.py:95-103、confirmation_store.py:64-71
- record_cs_turn 全链 fire-and-forget：conversation_store.py:48-49,213-216,234-235
- realtime publish 失败仅 debug：realtime.py:99-102
- supervisor metrics 异常静默：supervisor.py:294-295

### 重复实现（需合并）

| 重复项 | 位置 | 后果 |
|---|---|---|
| 确认流程**整段双实现且已漂移** | pending_handler.py:119-320 vs experts/action.py:177-325 | 仅前者有追问 retry 上限；快照加载降级时确认请求静默落入旧实现 |
| route_path→expert 映射三套 | graph_state.py:29-36、cs_prefilter.py:84-95、supervisor.py:387-393 | 契约不清 |
| 转人工触发检测三层 | cs_prefilter.py:41、experts/handoff.py:48、supervisor.py:131-145 | 同一意图 3 层判断 |
| 域关键词两套 | CS_DOMAIN_PATTERNS vs CS_DOMAIN_KEYWORDS | 配置重复维护 |
| reporter 快照两套 | reporter.py:174-187 vs context.build_reporter_snapshot（:107-120，生产无调用） | 字段集分叉 |

### 废弃/死代码

- coarse_router.py:6 死注释「LLM 兜底」无实现
- context.py:77-120 copy_cs_context / build_reporter_snapshot 生产无调用
- experts/action.py:55 user_id 死赋值（:66 覆盖）
- confirmation "pending_confirmation" 幽灵值：supervisor.py:158、pending_handler.py:22 接受，但全库无写入方

### 无法运行/高危断点

- 低置信度 FAQ 被拦截不进知识专家：confidence=(coarse+fine)/2（cs_router.py:67），fine 兜底 conf=0.3（fine_router.py:89），大量正确意图平均分 <0.6 被 Supervisor Layer1c 直接 finish（supervisor.py:179-186）→ 用户拿到泛化兜底——**知识库明明可答**。评分标尺与门槛未对齐。**P0**
- coarse 规则通道实际失效：2 命中=0.667<0.8 决定线（coarse_router.py:61,83）→ 绝大多数流量白付 embedding 往返（1~3.4s）。P1
- pending_handler 复用 experts/action._simulate_execute **私有函数**（pending_handler.py:181-184）。P2
- 确认意图关键词子串误判：「这个可以取消吗」判 CANCEL（confirmation.py:97-122，取消优先 :114-116）；「确认不要了」判取消（"不"在取消词表）。P1

---

## 8. 当前可用 / 部分可用 / 不可用功能

| 等级 | 功能 |
|---|---|
| **可用**（前后端对齐，已接通） | 消息发送+流式响应（/chat/stream SSE）；会话历史从后端恢复（/cs/conversations/my → hydrateFromServer）；满意度评分+统计；坐席队列/认领/回复/关闭四操作；WS ticket 签发-核销；conversation.waiting 事件广播；订单/物流/账户查询（真实 DB 直查，有 demo 沙盒映射） |
| **部分可用** | 确认流程（文本关键词可用但双实现漂移+误判风险+无幂等）；转人工（全链路通但超时纯响应式、事件无去重、两次 save 暴露中间态）；事件推送（单进程可用，多进程失联）；管理端权限（操作可用但无管理员闸+agent_id 自声明） |
| **不可用** | 确认卡 UI（死代码）；持久审计（表空）；Celery 异步任务（客服域零接入）；Redis 事件/锁/去重；跨机部署的 WebSocket；真实业务写操作（全部 simulate，明示未伪装）；多进程部署一致性（内存 Store + 进程内 Hub） |

---

## 9. 问题严重程度汇总

### P0（阻断企业级目标 / 资金与安全）

1. 置信度标尺错位 → 正常 FAQ 被拒答（supervisor.py:179-186 + fine_router.py:89）
2. CSConfirmCard 死代码 + 确认双实现漂移 → 剧本 C 断链
3. 确认动作无幂等（confirmation_repo.py:61-67 裸 UPDATE 无 WHERE state='pending'）→ 接真实业务即双执行
4. 坐席并发双认领（cs_admin.py:880-911 SELECT-then-UPDATE 无锁）
5. Store DB 写失败静默降级 cache-only → PostgreSQL 非唯一事实源（handoff_store.py:95-103、confirmation_store.py:64-71）
6. IDOR：客服端点无归属校验 + agent_id 自声明（cs_admin.py 多处）
7. 审计表零写入（006:62,104）
8. _has_existing_refund 失败返回 False 放行重复退款（refund_service.py:160-163）
9. WebSocket 未过网关（apisix.yaml 无 /ws/*）

### P1（一致性 / 可用性）

10. 状态写权分裂：complaint/handoff expert 与 supervisor 直写 handoff_store 绕过 StateTransitionService（自称唯一入口）→ 不发状态事件（experts/complaint.py:97-98、supervisor.py:332、state_transition.py:170-188）
11. Store 非原子 read-modify-write + 无单活跃唯一索引（handoff_store.py:140-150、confirmation_store.py:101-111）
12. 转人工/确认超时纯响应式，无后台扫描 → 工单永久悬挂（supervisor.py:372）
13. realtime 无 Redis pub/sub、无序列号去重补发（realtime.py:92-113）
14. 过期确认记成 cancelled 而非 expired（confirmation_repo.py:76-77）
15. StateTransitionService._async_apply 跨维度无事务、错误归集不回滚（state_transition.py:110-190）
16. 确认意图关键词误判（confirmation.py:97-122）
17. coarse 规则通道失效白付 embedding（coarse_router.py:61,83）
18. decision_layer 标注失真（supervisor.py:200 记 layer=3 实无 LLM）
19. demo_mode 全局开关任意用户映射 demo 客户（demo_mode.py:27-37）

### P2（健壮性 / 可观测 / 卫生)

20. except:pass 系列（见 §7）；_db_loop 10s 超时后协程仍执行（_db_loop.py:30）且全局单 loop 串行
21. checkpoint 清理无批次（checkpointer_cleanup.py:66-89）；两条 DELETE 不同事务
22. 映射/快照/死代码（见 §7）
23. 错误协议三套并存；CSGraphState.cs_route 裸 dict 丢类型（graph_state.py:54）
24. audit trigger 正则反解析（cs_graph_node.py:84-89）；HandoffStore O(n) 线性扫描（:59-74）

---

## 10. 推荐保留、合并、迁移和删除的模块

### 保留（骨架正确，与目标拓扑吻合）

- 图结构 9 节点骨架（loader→pending_handler→supervisor→experts→reporter 与任务书 4.3 一致）；循环护栏
- 状态机纯函数层（handoff.py / confirmation.py / state_machine.py 设计干净）
- CSRouter 门面 + coarse/fine 分层（确定性路由方向正确）；统一事件票据鉴权（realtime.py:73-86）；checkpointer TTL 清理
- 查询侧全部 service（真实 DB 直查）；APISIX gateway-auth 链；管理端 WS+轮询降级；用户端历史恢复

### 合并

- 确认流程：pending_handler 与 experts/action 二合一（以 pending_handler 为准，补齐 action 路径）
- route_path→expert 映射三套 → 一处（graph_state 单一映射表）
- 域关键词两套 → 一套配置
- 用户/坐席消息读取端点 → 按角色拆分或加归属校验
- 错误协议三套 → 统一 error-code 响应体（接入 errors.py 异常树到全局 handler）

### 迁移（存储/职责搬家）

- HandoffStore/ConfirmationStore：内存 L1 → PostgreSQL 权威 + 原子条件更新（WHERE state=... + 单活跃唯一索引）；内存仅作只读缓存
- 事件 Hub：进程内 → Redis pub/sub + 序列号/去重；WS 路由进 APISIX
- 转人工/确认超时：响应式 → Celery beat 定时扫描（复用现成 tasks 基建）
- 状态写入权：expert/supervisor 直写 → 收口 StateTransitionService（发事件）
- 审计：LangGraph state 内飘 → 落 audit_logs/agent_actions 表
- 确认入口：文本关键词 → CSConfirmCard HTTP API（保留关键词为降级）

### 删除

- context.py 死函数（copy_cs_context/build_reporter_snapshot 生产版）；experts/action 死赋值；coarse 死注释；pending_confirmation 幽灵值
- 三层转人工触发检测收敛为 prefilter 一层

---

## 审查结论

任务书 4.3 的目标骨架**已基本落地**（确定性路由为主、Supervisor 无独立状态真相、普通 FAQ 不经 LLM Supervisor、循环有护栏、状态机纯函数与任务书对齐），**不需要推倒重写**。失控点集中在四条主线：

1. **状态一致性**：内存 Store 静默降级 + 无幂等/锁/原子更新 + 审计空转（§9 P0-3/4/5/7）
2. **确认链路**：卡片死代码 + 双实现漂移 + 关键词误判（P0-2）
3. **路由标尺**：置信度错位导致 FAQ 拒答（P0-1）
4. **实时与部署**：WS 绕网关 + 事件无去重多进程失联 + Celery/Redis 客服域零接入（P0-9、P1-11/13）

下一步按任务书第 11 节进入 P1（基础设施与状态统一），重构计划见 `refactor-plan.md`。
