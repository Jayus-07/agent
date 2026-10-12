# ADR-0002：企业主助手 Conversation-first 与独立业务域隔离

| 项目 | 决策 |
| --- | --- |
| 状态 | **Accepted（目标架构已确认，代码未实施/未验收）** |
| 日期 | 2026-10-12 |
| 适用范围 | `/agent` 企业主助手及其与独立 Travel/Customer Service 产品的边界 |
| 不适用范围 | 旅游、客服内部对话与业务状态机的重做 |

> **阅读警告：本文是后续施工的目标决策，不是对当前生产行为的描述。** 现行执行链、配置和完成状态以源码、对应测试、PR 合并情况及真实 API/SSE 证据为准；不能把本文列出的目标直接标记为已实现。

## 背景与目标

现行主图以 Router、General Chat、Planner/Supervisor、RAG/SQL/Skill/Workflow 和领域注册表组成。General Chat 主要服务寒暄/能力咨询；主入口存在领域预过滤、低置信澄清及引导分支。它们是**现状**，并非目标产品契约。

企业主助手的**目标**是：自然、连续地理解用户问题；无须工具时直接交流；需要企业证据、数据或执行动作时按权限调用现有能力；复杂任务才动态规划。不能因匹配不到工具就答非所问或重复输出业务菜单，也不能为了自然对话编造企业事实。

## 决策

1. **三个独立产品边界**：`/agent` 是企业通用助手；`/travel` 与 Customer Service 是独立专业 Agent。它们可以共享 LLMProxy、RAG/SQL 技术底座、Tool Governance、Prompt/用量、Trace，但不自动共享专属业务操作权、数据、会话和状态。
2. **Conversation-first，而不是所有请求先调一次 LLM**：先结合用户输入、受信任的入口身份、会话与当前任务判断是否需要外部证据或副作用。CHAT 自然直答；企业私有知识走授权 RAG；结构化经营数据走授权 SQL/Data；报告/合法业务任务复用 Skill/Workflow；多步骤不确定任务才使用 Planner/Supervisor。复杂/复合请求可以组合路径。
3. **意图、执行方式、能力选择、业务终态分离**：逻辑意图可标为 CHAT / KNOWLEDGE / DATA_QUERY / BUSINESS_OR_REPORT / ACTION / CLARIFY / UNSUPPORTED，但不要求新增对应 LangGraph execution_mode。沿用 `RouteDecision`、现有投影与能力事实源，禁止创建第二份权威 Router。
4. **主助手的服务端能力范围**：Travel/客服专属 Graph、Tool、Workflow、私有 KB、用户会话及写操作不属于 `/agent` 的授权能力；主助手不宣传或自动 handoff 到这两个产品。对“杭州在哪里”“退款是什么意思”等一般概念可正常解释；正式行程创建/修改、客服退款/售后执行必须说明当前入口不能办理。经营分析/选品等是否允许由既有业务清单和服务端权限决定，不能连带屏蔽。
5. **隔离是执行契约而非 Prompt**：请求携带的 `domain_hint`、`tool_name`、`capability` 与 LLM/Planner 输出均不得单独产生权限。应由服务端可信产品/主体上下文在候选发现、路由/规划、Tool/MCP 执行及数据访问边界执行范围限制；保留仍被独立入口使用的共享 Registry 声明，不能全局删除 Travel/CS 能力。
6. **一致的会话语义**：复用 L1/L2/L3、`ConversationContext`、`FollowUpResolver` 与 Context Budget。支持“继续”“刚才第二点”“换个说法”以及从聊天切换到数据查询；不新建第二套持久记忆，也不把历史模型回复当成权威企业数据。
7. **真实结果优先于自然语言**：复用已有 Runtime/Tool/Error 契约与 Reporter。区分正常回答、无证据、空结果、拒绝、待确认、部分成功、超时、服务故障、取消；业务事实必须受证据/授权约束，写操作遵守确认、幂等、状态回查。SSE `delta`、`done.answer`、`answer_source`、History、Memory、Trace 的最终语义不得分叉。
8. **成本与可靠性约束**：普通问题不默认触发 Embedding、SQL、Tool、Planner/Supervisor 或额外的回答评审 LLM；只有必要时使用受治理的模型仲裁。沿用 PromptService 版本、统一 LLMProxy 预算计费、执行治理、观测和失败降级机制。

## 边界和取舍

- **不采用“万能总控 Agent 自动接管全部业务”**：独立旅游、客服工作台保有自己的自然语言分诊、RAG/Tool 和业务状态；本决策不评价或替换它们的实现。
- **不采用“关键词等于 Tool/域调用”**：领域名词可能只是一般知识提问；但若明确要求企业私有事实、数据或操作，不能用无依据的聊天回答替代。
- **不复制技术栈**：不另建 RAGPipeline、SQLAgent、Tool Runtime、Memory Store、模型计费或第二套能力表。
- **不全局封禁共有词或删除共享注册表**：服务端按入口和权限裁剪候选及执行，保持专业入口完整功能。
- **不将设计目标当作已上线能力**：普通问答质量、权限隔离、真实 API/SSE 和 Token 变化均须施工后实测。

## 施工依赖与分批边界（均为待实施）

此 ADR 仅冻结设计。**当前 B1-D Prompt 治理任务及其它在途 Runtime/RAG 任务不因本 ADR 被打断。** 待其达到安全交接点、核对最新 `main` 与开放 PR、取得施工任务授权后，由独立 worktree/PR 分批实施：

| 批次 | 目标 | 退出证据 |
| --- | --- | --- |
| Batch 1 | `/agent` Conversation 路径、Triage、服务端合法能力范围 | 普通对话不误用 Tool；RAG/SQL/报告能按需选路；Travel/CS 专属能力不可越域 |
| Batch 2 | 跨轮上下文、证据与统一结果表达 | 追问承接、空/失败/拒绝语义；SSE、Trace、History、Memory 同一终态 |
| Batch 3 | 收敛旧分支、Prompt 文档同步及真实端到端验收 | 精简 Golden、真实 API/SSE、权限与成本对账；配置级回滚演练 |

每批小 PR、定向测试优先；旧测试若固化错误产品行为，更新断言须说明契约差异。无灰度/影子双跑；切换前应有可执行的配置级回滚。**本 ADR 不授权 Codex 自动合并、不授权跨域写入或身份/数据库语义变更。**

## 验收门槛（尚未执行）

- **自然对话**：“什么是 MCP”“解释 RAG”“继续”“刚才第二点”正确回答、不循环菜单，不无故调用外部能力。
- **企业事实**：有权限的制度查询走证据 RAG、经营统计走授权 SQL；无证据/无数据/工具失败不得虚构或假成功。
- **独立域**：可聊一般旅游/退款概念；从 `/agent` 无法调用 Travel/CS 的专属执行路径；伪造 `domain_hint` 或直调 Tool/MCP/Planner 同样被服务端阻断；专业入口维持原有行为。
- **跨轮和错误**：多轮事实指代、复杂任务拆解与确认、取消/超时/权限拒绝；SSE/Trace/History/Memory 一致。
- **经营性指标**：真实请求对账路由归宿、Tool 调用、Prompt 版本、Token、p50/p95 延迟；安全越权和未经确认的写操作为零容忍阻断项。未实测的条目标记 NOT VERIFIED，而非 PASS。

## 事实源与文档维护

- 当前主图/路由：[`backend/orchestration/graph/`](../../../backend/orchestration/graph/) 与 [`backend/orchestration/router/`](../../../backend/orchestration/router/)。
- 现行 Runtime：[AI Runtime](../ai-runtime.md)；共享服务边界：[Module Boundaries](../module-boundaries.md)。
- 领域入口：[Travel](../../domains/travel.md)、[Customer Service](../../domains/customer-service.md)；能力事实源：[`capabilities.yaml`](../../../backend/orchestration/router/capabilities.yaml)。
- 安全和质量：[授权规范](../../auth/authorization-standard.md)、[失败降级](../failure-degradation.md)、[SSE 契约](../../contracts/api-event-lifecycle.md)、[测试策略](../../development/testing-guide.md)。

Batch 落地后由实现 PR 更新对应权威文档与状态。本 ADR 不维护动态 PR/SHA/环境配置，不替代任务进度记录。
