# AI Runtime

本文说明主图、Domain Graph 接入和跨请求运行契约；节点、路由策略与事件 schema 的最终事实源均为源码。

## 主图与路由

主图构建于 backend/orchestration/graph/builder.py。内置节点承担 Router、Tool Selector、Skill Executor、Workflow Executor、Planner、Critique、Supervisor、Reporter 和 General Chat；Skill 节点和 Domain Graph 节点经各自注册表发现，不在 builder 中复制清单。

主路由由 backend/orchestration/router/engine.py 及其类型/投影模块负责。Router 决定运行目标，执行节点负责调用对应 Runtime；direct、workflow、plan、clarify/general chat 与 Domain Graph 的分流以当前图边和路由实现为准。

能力 Fast Path 还须满足分数、top1/top2 分差、LOW 风险、清单白名单及请求权限门禁。其余候选交 Tool Selector 在域内注册候选中消歧；无法确认时转澄清。LLM 仅从动态合法候选中给出选择提示，模型自报分数不参与授权。Trace 保留候选、分数类型、分差、阻断原因、最终归宿和策略版本；缓存键包含会话状态、租户、权限及路由策略版本。

候选 `CapabilityScore` 同时传递服务端解析得到的 `risk`、`fast_path_enabled`、`permission_ready`，这些字段在引擎组装、JSON往返和V2投影后保留，供 Tool Selector 二次校验。缺失时默认为 UNKNOWN/false/false，不能把分数或候选名称当作权限；实际执行仍由 Tool Governance 复核产品与主体权限。

Capability 映射与治理声明维护于 backend/orchestration/router/capabilities.yaml。明确的“联网查找/搜索”进入 knowledge 粗域，再由细路由在已授权能力中选择 web.search；否定联网不构成联网强信号，也不绕过工具权限。Tool 的选择和执行必须经过现有 Tool Governance；细节见 [tool-skill-guide.md](../development/tool-skill-guide.md)。

## Domain Graph

Domain Graph 通过 backend/domains/__init__.py 与各域注册模块登记，builder 自动从 Domain Registry 建立图节点。新增域还须核对入口预过滤和 Runtime descriptor；不得假设仅注册即可自动接入所有用户入口。

各域拥有自己的状态、检查点策略、执行节点和 Reporter。域图执行失败或降级由域契约处理，不应被主图 Reporter 二次解释成虚假的业务成功。服务依赖按 [domain-service-map.md](domain-service-map.md) 查阅。

## 公共运行契约

- LangGraph state、RouteDecision 与兼容投影以 backend/orchestration/ 的现行 schema 为准；新增状态字段需检查状态投影、Checkpoint 与调用方。
- Checkpoint 的身份键和数据读写必须按用户、租户、会话隔离；只读查询不应覆盖可写工作流状态。主图每次执行使用不透明 `agent-<UUID>` thread_id，避免 session_id 写入 checkpoint 索引；该键不跨轮复用，当前没有按此键恢复中断主图执行的公开路径。
- SSE 事件结构与事件顺序以 backend/orchestration/graph/event_schema.py、具体事件生成器和 API 契约为准；新增事件须同步检查后端和消费端。
- 上下文预算事件的缓冲作用域包含 Product、Tenant、User、命名空间 Session 和 Request。后台记忆裁剪事件只有在完整作用域与当前请求完全一致时才能进入该 SSE；作用域缺失、请求已超时或晚到事件不会转交给后续请求。事件只允许发送约定的结构元数据，不携带问题原文或摘要正文。
- 主助手与客服分别使用 `/chat/agent/*`、`/chat/customer-service/*` 和 `/memory/agent/*`、`/memory/customer-service/*`。身份由注册路由固定，产品入口要求可信用户与租户；body `user_id`、`domain_hint` 不授予身份或能力。Agent Memory 保持存量 raw user/session key，由 `/memory/agent/*` 独占访问；客服历史读写既有 `customer_service.conversations/messages`，并用可信 user/tenant 校验归属。旧共享 `/chat/*` 仅提供公共对话，旧共享 `/memory/*` 不开放持久记忆。
- L2 会话所有权由 `chat_sessions.tenant_id/user_id/session_id` 共同界定；会话列表、单会话读写和删除均校验服务端身份的租户与用户。旧会话仅按 `auth.users` 权威映射租户，无法映射的记录保留在不可由运行时身份产生的 `quarantine` 范围。schema 由 `092_chat_session_tenant_scope.sql` 管理。旅游旧消息适配器把租户摘要并入内部 user key，兼容其专用调用路径。
- 产品专属 `/chat/{product}/stream` 的成功轮次持久化以 `Idempotency-Key` 为幂等键；未提供时使用服务端 `request_id`。Memory 在同一事务中提交 L2 问答、幂等收据与 L3 outbox；收据按 tenant/user/session 隔离，只保留键和问题的 SHA-256。相同键与问题复用原轮次；相同键绑定不同问题时拒绝再次写入。表结构由 `090_memory_turn_idempotency.sql` 管理，随会话/消息删除级联清除。
- L2 `chat_messages` 兼容保留普通 user/assistant 文本行；成功轮次的 `end_turn → save_turn` 以受控 LangChain 写入记录 `message_type`（human/ai）及 JSON `message_metadata`。通用写入也支持 tool，可恢复 `AIMessage.tool_calls` 和 `ToolMessage.tool_call_id` 等协议字段；恢复时始终以数据库消息 ID 生成 LangChain ID，旧行的空类型列继续按 user/assistant 还原。字段由 `091_chat_message_langchain_metadata.sql` 管理。当前主 Graph Runner 只暴露最终问答和结构化 step results，尚无可信 LangChain ToolMessage 流可接入。
- HTTP 请求完成、Tool 执行结果、Domain Graph 业务结果是不同状态；错误和部分成功须保留可解释结果。
- 产品专属流式对话的浏览器断连由 SSE 恢复层继续消费 producer；若 Runner 生成器本身被关闭，则设置取消信号停止后台图，避免无人消费时继续执行节点副作用。用户主动取消和图执行失败均不得写入成功轮次记忆。
- Trace 与日志应记录必要的运行归因、耗时和错误类别，不写入敏感参数。

## Node Runtime

专家和域内节点的通用执行生命周期由 `backend/core/node_runtime/` 提供：执行上下文 → start hook → 节点调用 → 错误/超时策略 → `NodeResult` →完成/错误 hook。它只处理节点级错误策略与超时，不承担 Tool 的重试、熔断、并发隔离或副作用治理。

`ExecutionContext` 与 `NodeResult` 是冻结的通用契约；`NodeResult` 只包含 `status`、`error`、`duration_ms`、`data`，领域结果类型由各域自己定义。观测 hook 负责域级 Trace/指标适配，改动时核对 `backend/tests/node_runtime/`。

## Workflow 总 Deadline

`@workflow(deadline_sec=...)` 为整个 DAG 声明总执行预算，覆盖并行层、步骤和重试；未声明时默认 900 秒。在线图入口将 `RequestContext` 显式交给 Workflow Runtime，总预算取 Workflow 配置与请求 `remaining_workflow_ms()` 中较早的截止时间；Tool Runtime 在调用方未显式传入时继承同一请求 Deadline。调度任务没有请求上下文时只使用 Workflow 预算。每个 Step 的实际 timeout 取单步配置与总 Deadline 剩余量的较小值；总 Deadline 到期优先于单步 `on_error`、重试和降级，停止后续层并以 `failed` 保存唯一终态，迟到或吞取消的 Step 结果不会写成成功。单步 timeout 且物理完成未知时不自动重试，避免旧尝试与重试并发；Workflow 只结束自身 span，不 finish 调用方复用的请求 Trace。取消同步 Provider 的等待后无法确认供应商侧是否停止，step failure 与 Trace 会记录 `physical_completion=unknown_after_cancellation`；LLM 调用自身的 usage 记录负责其后续结算或待对账。

## 上下文预算与跨请求状态

L1 短期历史由 `SHORT_TERM_MAX_MESSAGES` 和 `SHORT_TERM_MAX_TURNS` 分别限制消息数和用户轮数；裁剪以用户消息为轮次边界，轮内 AI/Tool 消息一并保留。L1 按稳定数据库消息 ID 去重；相同正文但不同 ID 的消息仍保留为独立历史。消息数上限用于淘汰旧的完整轮次；最新单轮即使超过该数也不拆分，实际模型输入仍由后续 Token Budget 硬门禁约束。模型调用前由 `backend/context_budget/` 统一估算输入 token、预留模型输出和安全空间，并对历史、RAG 证据及工具结果做有界裁剪或压缩。原始会话历史不因预检而改写；裁剪后仍超预算时，由模型代理按稳定错误契约拒绝发送。预算策略和 L4/L5 实现以该模块、配置及测试为准。

跨请求的轻量业务上下文通过 `ConversationContextRepository` 读写：Memory 后端用于测试/本地场景，Redis 后端承载共享热状态。调用方提交表达业务意图的 mutation；并发更新使用版本、run id 或 question id 做 CAS，不能用无条件整对象覆盖。Redis 上下文是有 TTL 的热状态，不替代持久业务记录或图 checkpoint；键按 tenant、user、conversation 隔离。后端不可用、过期或被驱逐时的恢复/降级路径以仓库实现和对应测试为准。

## 源码定位

- Router：backend/orchestration/graph/router_node.py、backend/orchestration/router/
- 主图：backend/orchestration/graph/builder.py
- Domain Graph 注册：backend/orchestration/domain_registry.py、backend/domains/
- SSE：backend/orchestration/graph/event_schema.py、backend/app/api/routes/chat.py
- Checkpoint 与状态投影：backend/orchestration/、各域状态与存储模块
- 上下文预算：backend/context_budget/、backend/infra/llm/proxy.py
- 会话轻状态：backend/orchestration/context/context_repository.py
- Node Runtime：backend/core/node_runtime/；契约测试：backend/tests/node_runtime/

修改公共契约前，读取其守卫测试以及 [Frozen Contracts](Frozen-Contracts.md)。

## 失败传播与结果语义

Runtime 的图路由决策、节点执行结果、Tool 执行结果、领域最终结果是**不同的状态面**。普通 Tool 连接失败，只有在当前用户目标确实无法安全满足时才应阻断；内部图状态错误或未处理异常不得被标成外部 Provider 的降级。

- Tool Runtime 负责 `ToolStatus` 和执行可靠性；业务适配边界必须验证 HTTP 200 body 的 `status=failed`、业务错误码、Schema、空结果。未校验的 Raw ToolResult `SUCCESS` 仅证明执行成功，不代表业务成功。
- Domain / Supervisor 根据本次 brief、权限和硬约束确定 REQUIRED、IMPORTANT、OPTIONAL，选择继续、标记 DEGRADED、BLOCKED 或内部 FAILED。领域具体状态字段以当前域实现为准。
- Reporter 不得擅自覆盖真实结果或将 failed envelope 当文本答案；SSE `done`、Trace、数据库状态及前端终态须与同一业务结果一致。新增事件/字段先检查现有 schema、消费端、断线恢复和兼容性。
- Agent handoff 是职责转交，不是绕过 Tool Governance/授权/确认的执行捷径；转交时应保留 trace、状态归因及用户目标的硬约束。
- 完整分类矩阵及验收见 [Tool/Provider 失败规范](failure-degradation.md)。
