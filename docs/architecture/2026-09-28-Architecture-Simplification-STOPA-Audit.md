# Architecture Simplification — STOP A 只读审计

审计日期：2026-09-28。范围仅为现状盘点；本轮不修改生产代码、数据库、前端、
checkpoint、SSE 或运行时行为。

## 结论

当前平台的稳定能力应保留。真实问题不是缺少更多 Agent，而是路由、Capability
metadata、Domain 展示语义和 Expert 执行基础设施存在重复表达。STOP A 后建议按阶段
推进，任何阶段完成前不得进入下一阶段。

## 当前真实生产调用链

```text
POST /chat/stream
  -> GraphRunner（Input Guard / Memory / Context / Follow-up）
  -> main graph router_node
     -> CS / Travel / Selection / Booking / Commerce prefilter
     -> hierarchical Router（启用时）或 legacy rule -> vector -> LLM Router
     -> direct: tool_selector -> skill_executor -> reporter
     -> workflow: workflow_executor -> reporter
     -> plan: planner -> critique -> supervisor -> Skill nodes -> reporter
     -> domain graph: domain adapter -> domain-owned reporter -> END
```

`router_node` 是主图唯一生产入口，但同时承载 Domain 预过滤、层级路由、legacy
回退和状态兼容。它是 STOP B 的收口边界，不应在 STOP A 改动。

## Router 盘点

| 组件 | 生产状态 | 真实职责 |
|---|---|---|
| `orchestration/graph/router_node.py` | 使用中 | 主图入口、预过滤、兼容状态写回 |
| `router/hierarchical.py` | 按开关使用 | 粗域分类、域内候选、Fast Path/灰区 selector |
| `router/router.py` + rule/vector/LLM | legacy/降级/shadow 使用 | 旧多级 Capability 决策 |
| CS/Travel/Selection/Commerce/Booking prefilter | 使用中 | 域入口规则与灰度/开关门禁 |
| `orchestration/workflow/router.py::TaskRouter` | 生产引用未发现，仅测试引用 | 旧 workflow-vs-agent 评分器，属于删除候选 |
| RAG/SQL/queue 内部 router | 使用中 | 子系统内部路由，不应并入 Agent Runtime Router |

因此“Router 有几套”的答案是：主图入口 1 套，Capability 决策有 hierarchical 与
legacy 两套，另有 5 类 Domain prefilter；TaskRouter 不在生产链。

## Registry 与 Capability metadata

- Capability 路由清单的事实源是 `backend/orchestration/router/capabilities.yaml`，
  已声明 name、skill、domain、risk、routed、fast_path、rule keywords、examples。
- Skill 类仍声明 `capabilities`、`description`、`params_schema`、`examples`，并由
  `skills/registry.py` 实例化；Planner/Critique 使用这份 Skill metadata。
- 因此当前 Capability metadata 至少有两份：manifest 路由元数据与 Skill 计划元数据。
  两者存在重复和漂移风险，不能在 STOP A 把“双源”写成已解决。
- Tool registry、Workflow registry、Domain registry、MCP registry 各自有合理边界，
  当前不应创建 UniversalRegistry。

静态盘点：17 个 capability、4 个 workflow、12 个 Skill 目录、34 个 `@tool`。

## Domain 盘点

当前注册条目：

| 注册名 | 实际边界 | 目标架构语义 |
|---|---|---|
| `customer_service` | 客服多轮状态、handoff、五类专家 | Customer Service Domain |
| `travel` | 行程规划、slot、validator、repair | Travel / planning |
| `travel_commerce` | 酒店/机票等商务查询 | Travel / commerce |
| `travel_booking` | 预订事务、恢复、对账、webhook | Travel / booking |
| `selection_funnel` | 选品漏斗状态机 | Selection Domain |

Travel commerce/booking 已有独立物理子图，不应在本轮物理合并；应只在 Registry、
管理端和文档增加 `parent_domain=travel`、`subflow=commerce|booking` 语义。

## Plan runtime 盘点

- `planner`：生成 Capability DAG，可调用 LLM。
- `critique`：规则检查 capability、edges、深度和异常，只有 anomaly 才 LLM fallback。
- `supervisor`：确定性 DAG 调度、`Send` 并行、依赖解析、`previous_outputs` 和失败传播。

后两者不是 Agent：目标语义应是 `PlanValidator` 与 `PlanExecutor`；但 `critique`、
`supervisor` LangGraph node id 已进入 checkpoint/trace/evaluation，本轮不能改名。

## Expert runtime 盘点

`customer_service/experts/base.py` 与 `travel/experts/base.py` 都实现计时、日志、
异常转换、状态封装和 trace/metrics，但实现并不相同：CS 还有线程超时、contextvars
和 CS 指标；Travel 还有专家 span。业务 Result 类型差异合理，公共安全执行生命周期
适合抽到 `core/node_runtime`，但需保留这些 hook。

## Tool / Reporter / MCP 盘点

- 旧 Tool 返回 JSON、dict、str、Markdown 混合；已确认 `backend/tools/sql.py` 返回
  Markdown，map/data collection 也存在渲染型或非统一输出。
- Markdown 应归 Reporter/Response Composer。STOP G 只能新增 `ToolResult` 与兼容
  normalizer，不能一次重写 34 个旧 Tool。
- MCP 当前为 RAG、SQL 两个 server；`schema_adapter.py` 从 LangChain Tool 的
  `args_schema` 派生参数。MCP 应定位为外部 Integration Adapter，不是内部第五层。
- Java integration 应继续 REST/gRPC/Event 边界；本审计未发现应改成 MCP 的生产链路。

## STOP A 问题清单答案

1. Router：主入口 1、Capability 决策 2、Domain prefilter 5 类；TaskRouter 非生产。
2. 生产使用 `router_node`、prefilter、hierarchical/legacy；TaskRouter 仅测试。
3. Capability metadata 两份；Skill 与 Capability 重复 description/examples/params。
4. TaskRouter 无生产价值，可在 STOP B 先替换测试再删除。
5. Travel 的规划/商务/预订是一个顶级 Domain 的三个 subflow。
6. CS/Travel Expert runtime 有重复执行生命周期，但业务 Result 不应强行统一。
7. Planner 是需要智能的拆解器；Validator、Executor、Skill、Tool、Reporter、Expert
   runtime 都是普通运行时/执行器，不应统称 Agent。

## 保护边界

以下内容在后续阶段也必须保持不变：`/chat/stream`、SSE frame protocol、前端三端、
数据库 schema/migration、checkpoint、Celery task runtime、RAG/SQL/Travel/CS evaluation、
权限与幂等模型、RAG service 资源隔离，以及现有 LangGraph node id。

## STOP A 交付状态

- 已完成当前真实调用链、Router、Registry、Capability、Skill、Tool、Workflow、Domain、
  Expert runtime、MCP 和遗留候选的只读盘点。
- 本轮未进入 STOP B–H，未做代码架构重构。
- 当前工作区仅保留任务开始前已存在的三个前端 `tsconfig.json` 修改；本审计新增本文件。
