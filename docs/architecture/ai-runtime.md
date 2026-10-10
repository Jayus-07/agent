# AI Runtime

本文说明主图、Domain Graph 接入和跨请求运行契约；节点、路由策略与事件 schema 的最终事实源均为源码。

## 主图与路由

主图构建于 backend/orchestration/graph/builder.py。内置节点承担 Router、Tool Selector、Skill Executor、Workflow Executor、Planner、Critique、Supervisor、Reporter 和 General Chat；Skill 节点和 Domain Graph 节点经各自注册表发现，不在 builder 中复制清单。

主路由由 backend/orchestration/router/engine.py 及其类型/投影模块负责。Router 决定运行目标，执行节点负责调用对应 Runtime；direct、workflow、plan、clarify/general chat 与 Domain Graph 的分流以当前图边和路由实现为准。

能力 Fast Path 还须满足分数、top1/top2 分差、LOW 风险、清单白名单及请求权限门禁。其余候选交 Tool Selector 在域内注册候选中消歧；无法确认时转澄清。LLM 仅从动态合法候选中给出选择提示，模型自报分数不参与授权。Trace 保留候选、分数类型、分差、阻断原因、最终归宿和策略版本；缓存键包含会话状态、租户、权限及路由策略版本。

Capability 映射与治理声明维护于 backend/orchestration/router/capabilities.yaml。Tool 的选择和执行必须经过现有 Tool Governance；细节见 [tool-skill-guide.md](../development/tool-skill-guide.md)。

## Domain Graph

Domain Graph 通过 backend/domains/__init__.py 与各域注册模块登记，builder 自动从 Domain Registry 建立图节点。新增域还须核对入口预过滤和 Runtime descriptor；不得假设仅注册即可自动接入所有用户入口。

各域拥有自己的状态、检查点策略、执行节点和 Reporter。域图执行失败或降级由域契约处理，不应被主图 Reporter 二次解释成虚假的业务成功。服务依赖按 [domain-service-map.md](domain-service-map.md) 查阅。

## 公共运行契约

- LangGraph state、RouteDecision 与兼容投影以 backend/orchestration/ 的现行 schema 为准；新增状态字段需检查状态投影、Checkpoint 与调用方。
- Checkpoint 的身份键和数据读写必须按用户、租户、会话隔离；只读查询不应覆盖可写工作流状态。
- SSE 事件结构与事件顺序以 backend/orchestration/graph/event_schema.py、具体事件生成器和 API 契约为准；新增事件须同步检查后端和消费端。
- HTTP 请求完成、Tool 执行结果、Domain Graph 业务结果是不同状态；错误和部分成功须保留可解释结果。
- Trace 与日志应记录必要的运行归因、耗时和错误类别，不写入敏感参数。

## Node Runtime

专家和域内节点的通用执行生命周期由 `backend/core/node_runtime/` 提供：执行上下文 → start hook → 节点调用 → 错误/超时策略 → `NodeResult` →完成/错误 hook。它只处理节点级错误策略与超时，不承担 Tool 的重试、熔断、并发隔离或副作用治理。

`ExecutionContext` 与 `NodeResult` 是冻结的通用契约；`NodeResult` 只包含 `status`、`error`、`duration_ms`、`data`，领域结果类型由各域自己定义。观测 hook 负责域级 Trace/指标适配，改动时核对 `backend/tests/node_runtime/`。

## 上下文预算与跨请求状态

模型调用前由 `backend/context_budget/` 统一估算输入 token、预留模型输出和安全空间，并对历史、RAG 证据及工具结果做有界裁剪或压缩。原始会话历史不因预检而改写；裁剪后仍超预算时，由模型代理按稳定错误契约拒绝发送。预算策略和 L4/L5 实现以该模块、配置及测试为准。

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
