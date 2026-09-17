# 企业级智能客服系统重构任务书

> 状态：**生效中（PERSISTED）**
> 持久化时间：2026-09-17 20:15
> 持久化原因：用户明确要求「把我的任务书持久化，对着任务书来实施，结果和任务书一模一样」。后续所有会话必须以本文件为唯一实施依据。

---

## 1. 角色与总目标

你是当前项目的**企业级客服平台架构负责人、资深后端工程师和全栈工程师**。

请接手当前项目，对现有智能客服系统进行全面代码审查、架构收敛、功能修复和端到端验收。

当前项目技术栈：

* Backend：FastAPI + LangGraph + LangChain
* Frontend：Next.js 14 + React + Zustand
* Gateway：Apache APISIX
* Async：Celery
* Cache / Broker：Redis
* Database：PostgreSQL
* AI：LLM + RAG
* 实时通信：SSE + WebSocket

当前系统经过多个 Agent 反复修改，存在架构复杂、职责重复、功能断链、状态不一致、部分流程无法跑通等问题。

**最终目标：不是增加更多 Agent，而是把现有客服系统重构为一个可运行、可恢复、可观测、可测试、可演示的企业级客服平台。**

---

## 2. 必须遵守的原则

1. 先审查现有代码，再制定重构方案，最后实施。不得凭空假设项目结构。
2. 不要直接推倒重写整个项目。优先保留已有可用能力，重构失控部分。
3. 不要继续无目的增加 Agent、Supervisor、Router、状态 Store 或中间层。
4. 不得为了通过测试而删除测试、降低验收标准、屏蔽异常或伪造成功结果。
5. 不得破坏现有 RAG、主图、统一认证、APISIX、前端和其他业务模块。
6. 任何高风险改动必须先说明影响范围、迁移策略和回滚方案。
7. 所有关键功能必须有真实的端到端验证，不能只验证函数或接口返回 200。
8. 所有业务写操作必须有明确的权限校验、幂等机制、审计记录和失败处理。
9. PostgreSQL 是客服业务事实来源。Redis 只能承担缓存、队列、临时协调和事件通信等职责。
10. LangGraph 负责需要状态、分支、暂停、恢复或复杂推理的流程；普通确定性业务不要强行交给 LLM 决策。

---

## 3. 第一阶段：全面审查，禁止立即改代码

先完整检查：

### 后端

* FastAPI 路由、依赖注入和异常处理
* APISIX 路由、认证和身份传递
* GraphRunner、主图、客服子图和适配器
* CSRouter、Supervisor、所有客服专家
* LangGraph State、checkpoint、Command、interrupt、恢复逻辑
* RAGPipeline、LLM 调用和超时机制
* PostgreSQL 模型、事务、迁移和数据访问层
* Redis、Celery Broker、Worker、任务状态和重试机制
* SSE、WebSocket、实时事件广播
* 转人工、确认状态机、业务动作、权限和审计

### 前端

* 用户客服入口和消息发送
* SSE 消费、错误处理、取消请求和断线恢复
* Zustand 状态管理和历史会话恢复
* 确认卡、转人工状态、人工消息展示
* 管理端队列、会话列表、认领、回复和关闭
* WebSocket、轮询降级和鉴权

### 必须输出

先生成：

`docs/customer-service/audit-report.md`

报告至少包括：

1. 当前真实架构图和请求链路。
2. 所有客服入口及 API 调用关系。
3. 所有状态来源和状态转换关系。
4. LangGraph 节点、边、循环和 checkpoint 关系。
5. Celery、Redis、PostgreSQL 的实际使用情况。
6. 前后端功能断链清单。
7. 无法运行、异常吞掉、重复实现和废弃代码清单。
8. 当前可用功能、部分可用功能和不可用功能。
9. 问题严重程度：P0 / P1 / P2。
10. 推荐保留、合并、迁移和删除的模块。

**审查完成后先输出报告和重构计划，不要未经确认大规模修改代码。**

---

## 4. 目标架构

将客服系统收敛为独立的 Customer Service Platform。

### 4.1 入口层

APISIX：

* 统一 API 入口。
* JWT / API Key 鉴权。
* 用户身份注入。
* 限流、CORS、请求 ID 和基础日志。
* API / SSE / WebSocket 路由。

不得在 APISIX Lua 中实现客服业务路由、LLM 调用、Celery 调度或业务状态机。

FastAPI：

* 用户会话 API。
* 消息 API。
* 转人工 API。
* 确认动作 API。
* 坐席工作台 API。
* 任务状态 API。
* 统一异常和响应协议。

保留现有接口兼容层，待新接口完成真实验收后再迁移前端。

### 4.2 应用服务层

建议形成清晰结构：

```text
backend/customer_service/
├── api/
├── application/
│   ├── conversation_service.py
│   ├── message_service.py
│   ├── handoff_service.py
│   ├── confirmation_service.py
│   └── task_service.py
├── domain/
│   ├── conversation_state.py
│   ├── handoff_state.py
│   ├── confirmation_state.py
│   └── business_action_state.py
├── graph/
│   ├── builder.py
│   ├── state.py
│   ├── router.py
│   └── nodes/
├── services/
│   ├── knowledge_service.py
│   ├── order_service.py
│   ├── logistics_service.py
│   └── action_service.py
├── repositories/
├── tasks/
├── realtime/
├── security/
└── observability/
```

实际目录以现有项目为准。不要为了目录美观而机械搬迁所有代码。

### 4.3 LangGraph

将现有 9 节点客服图收敛为：

```text
接收消息
    ↓
加载持久化会话状态
    ↓
人工 / 待确认状态判断
    ↓
确定性 IntentRouter
    ↓
领域处理器
    ├── Knowledge：RAG 问答
    ├── Query：订单、物流、账户查询
    ├── Action：业务动作提案与确认
    ├── Complaint：投诉工单
    └── Handoff：转人工
    ↓
统一响应组装
    ↓
持久化 + 事件发布 + Trace
```

要求：

* 普通 FAQ 不调用 LLM Supervisor。
* 路由结果必须结构化、可追踪、可测试。
* Supervisor 不得拥有独立且隐式的状态真相。
* 专家不得绕过 Service 层直接访问数据库或外部 HTTP。
* 不允许无限循环、隐式递归或无限重试。
* 所有图节点必须有明确输入、输出、超时和失败行为。
* 保留客服域与主图的兼容适配器，但逐步减少重复路由。

---

## 5. 状态管理重构

### 5.1 PostgreSQL 唯一业务事实源

必须持久化：

* 会话和消息。
* 用户、坐席和分配关系。
* 转人工工单。
* 确认动作和业务动作。
* 任务记录及执行结果。
* 审计记录。
* LangGraph checkpoint。
* 必要的 Trace 和评测结果。

Redis 不得成为唯一业务状态来源。

### 5.2 状态机必须明确

转人工：

```text
AI_ACTIVE
→ HANDOFF_REQUESTED
→ WAITING_HUMAN
→ HUMAN_ACTIVE
→ CLOSED
```

支持合法的取消、超时回退和关闭转换。

业务动作：

```text
PROPOSED
→ WAITING_CONFIRMATION
→ CONFIRMED
→ EXECUTING
→ SUCCEEDED / FAILED
```

支持：

* CANCELLED
* EXPIRED
* 失败补偿
* 幂等执行
* 审计记录

不要将转人工状态、会话状态和业务动作状态混成一个巨型字段。

### 5.3 必须解决

* 内存 Store 与数据库状态不一致。
* checkpoint 残留导致状态污染。
* 用户重复提交导致重复执行。
* 坐席重复认领。
* 用户断线后消息丢失。
* Worker 重启后任务无法恢复。
* Redis 重启后关键状态丢失。
* 多进程并发更新覆盖状态。

所有状态转换必须有明确的事务、并发控制和失败策略。

---

## 6. Celery、Redis、LangGraph 的职责

### Celery

仅负责后台任务：

* RAG 文档索引。
* 长耗时外部服务调用。
* 报表和批量任务。
* 通知发送。
* 过期状态清理。
* 失败补偿和重试。

每个任务必须具备：

* task_id
* 业务幂等键
* 状态记录
* 重试策略
* 超时控制
* 最大重试次数
* 失败原因
* 可追踪的 Trace

不得让 Celery Task 和 LangGraph Supervisor 互相嵌套成为另一套编排系统。

### Redis

用于：

* Celery Broker / Backend。
* 缓存。
* 限流。
* 短期锁。
* 事件发布。
* 临时连接协调。

必须设计 Redis 故障策略：

* 缓存失效不影响数据库事实。
* 关键业务写入不能因为 Redis 失败而静默成功。
* 不允许通过"吞掉异常"掩盖队列不可用。
* 明确哪些功能降级、哪些功能拒绝执行。
* 恢复后可补偿未发送的事件。

### LangGraph

用于：

* 有状态客服流程。
* 需要暂停和恢复的流程。
* 人工确认。
* 复杂业务推理。
* 多步骤领域任务。

不要把所有普通 CRUD、简单路由和每条消息都包装成复杂 Agent。

---

## 7. 必须打通的核心业务闭环

优先实现并验证以下五个剧本：

### 剧本 A：知识问答

用户提问 → APISIX → FastAPI → LangGraph → RAG → 引用校验 → SSE → 前端展示。

要求：

* 真实检索。
* 正确引用。
* 低证据拒答。
* 超时和异常有可读反馈。
* Trace 记录完整。

### 剧本 B：订单和物流查询

用户查询 → 身份校验 → 业务 Service → PostgreSQL → 结构化结果 → 前端展示。

要求：

* 演示账号有真实种子数据。
* 不能查询其他用户数据。
* 指定订单号时必须查询指定订单。
* 多订单场景不能默认取最新订单冒充答案。
* 无数据时明确反馈。

### 剧本 C：退款 / 退货确认

用户提出动作 → 资格检查 → 生成 proposal → 保存待确认状态 → 前端确认卡 → 用户确认 → 幂等执行 → 保存结果 → 返回结果。

要求：

* 接通现有 `CSConfirmCard`。
* 不得只依赖文本"确认 / 取消"。
* 高风险动作必须确认。
* 真实业务未接通时，必须明确使用演示沙盒。
* simulate 不得伪装成真实业务成功。

### 剧本 D：转人工

用户请求转人工 → 创建工单 → WAITING_HUMAN → 坐席队列 → 坐席认领 → HUMAN_ACTIVE → 坐席回复 → 用户收到消息 → 关闭会话。

要求：

* 工单状态真实持久化。
* 队列可查询。
* 认领幂等。
* 用户后续消息不会被 AI 抢答。
* SSE / WebSocket 断线后可恢复。
* 无在线坐席时有明确提示。
* 演示模式可以使用模拟坐席，不要求真实人工在线。

### 剧本 E：异步任务

创建任务 → Celery 排队 → Worker 执行 → 状态更新 → 前端查询 / 事件通知 → 完成或失败。

要求：

* Worker 重启后可恢复或明确失败。
* 任务不会无限重试。
* 重复消息不产生重复业务动作。
* 任务状态可以查询。
* 失败有可操作的错误信息。

---

## 8. 前端和实时通信

用户端：

* 保留现有客服入口。
* 统一消息发送和流式响应。
* 接入确认卡。
* 支持会话历史恢复。
* 支持错误、取消和断线处理。
* 不仅依赖 Zustand 内存状态。
* 重要状态从后端恢复。

坐席端：

* 队列列表。
* 会话详情。
* 认领、回复、关闭。
* 实时事件。
* WebSocket 失败时轮询降级。
* 权限和身份校验。

统一事件格式：

```json
{
  "event_id": "event-id",
  "event_type": "message.created",
  "conversation_id": "conversation-id",
  "task_id": null,
  "trace_id": "trace-id",
  "sequence": 1,
  "payload": {}
}
```

要求事件可去重、可追踪，避免前端重复显示消息。

---

## 9. 可观测性与错误处理

每次请求必须尽可能关联：

* request_id
* conversation_id
* message_id
* trace_id
* task_id
* graph_run_id

必须记录：

* 路由结果和置信度。
* 实际执行的领域处理器。
* RAG 检索、重排、引用和拒答信息。
* LLM 调用、耗时和 Token。
* Celery 任务状态。
* 数据库操作错误。
* 转人工状态变化。
* 业务动作审计。
* 错误类型和恢复结果。

禁止：

* `except: pass`
* 无日志吞异常。
* 返回"处理中"但后台没有真实任务。
* 将模拟执行标记为真实成功。
* 用静态假数据冒充生产业务结果。
* 为了让前端显示成功而绕过后端状态。

---

## 10. 测试与验收

建立独立客服测试目录：

```text
backend/tests/customer_service/
├── test_api.py
├── test_router.py
├── test_graph_e2e.py
├── test_handoff_state.py
├── test_confirmation_state.py
├── test_idempotency.py
├── test_celery_tasks.py
├── test_realtime.py
├── test_failure_recovery.py
└── fixtures/
```

必须覆盖：

### Happy Path

* FAQ 问答。
* 订单查询。
* 退款确认。
* 转人工。
* 坐席回复。
* 任务完成。

### Error Path

* LLM 超时。
* RAG 失败。
* 数据库不可用。
* Redis 不可用。
* Worker 失败。
* WebSocket 断开。
* 未授权访问。
* 无效确认。
* 重复提交。

### Edge Path

* 多订单查询。
* 用户连续发送消息。
* 坐席重复认领。
* 同一动作重复确认。
* 确认过期。
* 转人工超时。
* 用户刷新页面。
* 服务重启后恢复。
* 多进程并发状态更新。

必须至少完成一次真实 E2E：

```text
用户前端
→ APISIX
→ FastAPI
→ LangGraph
→ RAG / 业务 Service
→ PostgreSQL
→ SSE / WebSocket
→ 前端最终展示
```

不要只运行单元测试就宣布功能完成。

---

## 11. 实施顺序

### P0：审查与基线

* 输出完整审查报告。
* 盘点现有代码和功能。
* 固定演示环境和测试数据。
* 建立启动、健康检查和冒烟测试。
* 找出 P0 阻断问题。

### P1：基础设施与状态统一

* 统一 API 和异常协议。
* 统一数据库状态模型。
* 清理重复 Store。
* 完善事务、幂等和迁移。
* 确认 APISIX、Redis、PostgreSQL、Celery 的真实运行方式。

### P2：LangGraph 和 Celery 收敛

* 简化客服图。
* 合并重复 Router / Supervisor。
* 拆分业务 Service。
* 完善任务状态、重试和恢复。
* 完成暂停、恢复和失败处理。

### P3：前后端业务闭环

* 接通确认卡。
* 打通订单、物流和演示数据。
* 打通转人工队列和坐席回复。
* 完善 SSE、WebSocket 和轮询降级。
* 验证前端状态与数据库一致。

### P4：验收与生产化

* 完成 Graph E2E、API、任务和故障测试。
* 完成 Trace、日志和监控。
* 完成 Redis / Worker / 数据库故障演练。
* 输出部署手册、迁移记录和回滚方案。
* 通过所有核心业务验收后，才允许继续扩展功能。

---

## 12. 最终交付物

必须提交：

1. `docs/customer-service/audit-report.md`
2. `docs/customer-service/target-architecture.md`
3. `docs/customer-service/refactor-plan.md`
4. `docs/customer-service/api-contract.md`
5. `docs/customer-service/state-machine.md`
6. `docs/customer-service/runbook.md`
7. 完整代码改动和数据库迁移。
8. 自动化测试及测试报告。
9. 端到端验收记录。
10. 已知限制、未完成项和后续计划。

每个阶段结束时必须汇报：

* 本阶段修改了什么。
* 修改了哪些文件。
* 为什么这样修改。
* 哪些功能已验证。
* 执行了哪些测试及结果。
* 还存在什么问题。
* 是否影响现有主图、RAG、认证和其他业务。
* 下一阶段准备做什么。

**完成标准：**

客服系统能够在现有项目中稳定启动，用户可以真实完成知识问答、订单查询、业务确认和转人工，坐席可以处理工单，异步任务可以追踪和恢复，Redis 或 Worker 故障不会造成静默数据丢失，所有核心链路有自动化测试和可观测记录。

不要以"代码已重构""接口返回成功"或"测试通过"作为唯一完成依据。以真实业务闭环和端到端验收结果为准。
