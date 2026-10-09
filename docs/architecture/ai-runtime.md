# AI Runtime

本文说明主图、Domain Graph 接入和跨请求运行契约；节点、路由策略与事件 schema 的最终事实源均为源码。

## 主图与路由

主图构建于 backend/orchestration/graph/builder.py。内置节点承担 Router、Tool Selector、Skill Executor、Workflow Executor、Planner、Critique、Supervisor、Reporter 和 General Chat；Skill 节点和 Domain Graph 节点经各自注册表发现，不在 builder 中复制清单。

主路由由 backend/orchestration/router/engine.py 及其类型/投影模块负责。Router 决定运行目标，执行节点负责调用对应 Runtime；direct、workflow、plan、clarify/general chat 与 Domain Graph 的分流以当前图边和路由实现为准。

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

## 源码定位

- Router：backend/orchestration/graph/router_node.py、backend/orchestration/router/
- 主图：backend/orchestration/graph/builder.py
- Domain Graph 注册：backend/orchestration/domain_registry.py、backend/domains/
- SSE：backend/orchestration/graph/event_schema.py、backend/app/api/routes/chat.py
- Checkpoint 与状态投影：backend/orchestration/、各域状态与存储模块

修改公共契约前，读取其守卫测试以及 [Frozen Contracts](Frozen-Contracts.md)。