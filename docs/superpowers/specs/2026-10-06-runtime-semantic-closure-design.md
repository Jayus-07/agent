# Runtime 语义收口设计

日期：2026-10-06  
范围：STOP A → STOP B → STOP C  代码基线：`feat/domain-isolation-closure`

## 1. 目标

将现有主图从“`route_mode` 同时表示业务域、执行方式和交互控制”收敛为四个正交维度：

```text
Domain → Execution → Interaction → Runtime Target
```

新契约作为事实源，旧状态字段继续通过唯一兼容投影生成。目标是完成架构语义收口，而不是更换主图架构。

本轮不物理迁移 LangGraph 节点，不重建 RoutingEngine，不改变任何既有运行时的执行行为。

## 2. 已确认的硬约束

- 不修改 `router`、`route_selector`、`planner`、`critique`、`supervisor` 等既有 node_id。
- 不修改 SSE event name、checkpoint key、pending Send、前端 node mapping。
- 不把 Travel 或 Selection 改成 Agent Runtime。
- 不统一 CS、Travel、Selection 的内部 State 或子图结构。
- 不新建第二套 Domain/Runtime/Subgraph Registry。
- 不重写 Tool、Skill、MCP 或 Provider 架构。
- 工作区已有的未提交报告、probe 文件和截图不属于本设计，必须保留。

## 3. 目标契约

### 3.1 路由决策

在现有 `backend/orchestration/router/types.py` 中新增可序列化的 V2 模型：

- `RuntimeType`：`agent_runtime`、`workflow_runtime`、`plan_runtime`、`direct_runtime`、`generic_runtime`。
- `InteractionMode`：`execute`、`guide`、`clarify`、`handoff`。
- `DomainDecisionV2`：`name`、`subflow`、`confidence`、`source`、`reasoning`。
- `ExecutionDecision`：沿用既有 `direct`、`workflow`、`plan`，不加入业务域名称。
- `RuntimeTarget`：`type`、`id`、`subflow`。
- `RouteDecisionV2`：`domain`、`intent`、`execution`、`interaction`、`runtime`、`capability`、`workflow_name`、`confidence`、`reason`。

现有 `RouteDecision` 保留，作为兼容输入/输出模型，暂不删除或重命名。

### 3.2 Runtime 入口/出口契约

新增纯数据模型：

- `RuntimeContext`：请求级 session、tenant、用户、问题、路由决策及可序列化上下文。
- `RuntimeResult`：`status`、`answer`、`answer_type`、`sources`、`artifacts`、`tool_calls`、`ui_payload`、`clarification`、`handoff`、`error`、`metadata`。

本轮只冻结模型，不要求 CS、Travel、Selection 立即改为返回 `RuntimeResult`；该接线属于后续 STOP D。

### 3.3 Runtime Descriptor

不新建 Registry。扩展现有 `DomainGraph` 描述模型，新增兼容字段：

- `runtime_id`
- `runtime_type`
- `aliases`
- `capabilities`
- `supports_checkpoint`
- `supports_interrupt`
- `supports_streaming`
- `result_contract_version`
- `entry_modes`
- `continuation_policy`

新增字段均提供默认值，保证当前各域的注册调用和测试构造方式不变。`DomainGraphRegistry` 继续是唯一注册入口。

## 4. 生产数据流

主图仍使用原有节点和条件边，只有路由状态的生成方式改变：

```text
Router / Prefilter / Continuation
            ↓
      RouteDecisionV2
            ↓
project_route_decision_to_legacy_state()
            ↓
route_decision_v2 + route_mode + legacy fields
            ↓
现有 route_selector / DomainGraph / Built-in Runtime
```

新增 `route_decision_v2` 进入主图 State，作为新事实源。以下字段继续存在，但只允许由 Projection 产生：

- `route_decision`
- `route_mode`
- `domain` 及域置信度字段
- `candidate_tools`
- `selected_tool`
- `need_clarification` / `clarification_reason`
- `executor_mode` / `executor_workflow` 等兼容字段

投影必须覆盖所有 Router 出口：RoutingEngine 正常路径、域 prefilter、continuation、general chat、clarify 和 handoff。旧 `route_mode` 的值与下一节点映射保持不变。

## 5. 兼容语义

### 5.1 执行模式

`ExecutionDecision.mode` 只表达现有主图执行族：

```text
direct | workflow | plan
```

域运行时的具体归属由 `RuntimeTarget` 表达。为保持现有主图兼容，域图入口继续通过投影生成原有域名 `route_mode`，不要求 `route_selector` 读取 V2。

### 5.2 交互模式

```text
execute  → 正常执行
guide    → 生成现有 handoff 路径
clarify  → 生成现有 clarification 路径
handoff  → 生成现有 handoff 路径
```

`guide`、`clarify`、`handoff` 不进入 `ExecutionMode`，也不新增主图 node。

### 5.3 运行时类型

当前域的默认元数据：

| runtime_id | domain | runtime_type |
|---|---|---|
| `customer_service` | `customer_service` | `agent_runtime` |
| `travel` | `travel` | `workflow_runtime` |
| `selection_funnel` | `selection` | `workflow_runtime` |
| `selection_decision` | `selection` | `workflow_runtime` |

`general_chat`、direct、plan 等主图路径通过同一 V2 契约表达，但不强行改造成 DomainGraph。

## 6. Registry 收口

由 `DomainGraphRegistry` 派生以下视图，替代 Router 中可注册的重复字典：

- route mode → 顶级 domain
- route mode → subflow
- route mode → runtime target
- route mode → domain family
- route mode → entry mode config
- aliases → canonical runtime

保留在业务模块中的逻辑：

- 旅游一次性查询判据；
- CS continuation 与锁域规则；
- booking resolver；
- 各域 prefilter 的具体识别实现。

注册表只描述归属和运行时能力，不承担业务规则判断。

## 7. 错误与降级

- V2 模型构造失败：在 Router 入口记录明确错误并沿用现有安全澄清/兼容路径。
- Projection 缺少可选元数据：使用现有旧字段默认值，不改变路由结果。
- Registry 元数据缺失：注册阶段 fail-fast；读取阶段不得静默拼接域名。
- 新字段必须满足 LangGraph State schema，禁止依赖 schema 外临时键。
- 不在请求内重建索引、不重复调用 RoutingEngine、不增加 Runtime 双跑。

## 8. 测试与验收

### STOP A

- 枚举和模型字段校验；
- Pydantic/字典序列化与 checkpoint-safe 数据；
- RuntimeDescriptor 默认值兼容；
- 非法 Runtime/Interaction 组合拒绝或明确降级。

### STOP B

- 所有既有 route_mode 值的投影快照；
- RoutingEngine、prefilter、continuation、general_chat、clarify、handoff 均写入同一 V2 字段；
- V2 与旧字段的一致性守卫；
- route_selector 输出、SSE、节点拓扑回归不变。

### STOP C

- Registry 是域归属唯一事实源；
- 顶级域、subflow、runtime target 派生正确；
- 重复 runtime_id、未知 alias、非法父子域关系 fail-fast；
- Router 不再维护可注册域的重复映射；
- 新增域只需注册描述符、实现 prefilter、补测试。

局部测试命令必须带 `--no-cov`。

## 9. 本轮完成标准

```text
CONTRACT_V2_PASS=true
ROUTE_MODE_BACKWARD_COMPAT_PASS=true
ROUTE_DECISION_V2_PASS=true
RUNTIME_REGISTRY_PASS=true
DOMAIN_REGISTRATION_SINGLE_SOURCE_PASS=true
LEGACY_BEHAVIOR_CHANGED=false
```

本轮完成后仍不宣称 RuntimeResult 已接入所有域；那属于 STOP D，不纳入本设计。
