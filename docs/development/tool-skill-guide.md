# Agent、Skill、Tool、Workflow 与 MCP 开发规范

本文件是新增和修改这些运行资产的主要开发入口。实际接线以当前代码和声明文件为准；禁止照搬已过期文档中的旧注册步骤或代码模板。

## 职责边界

| 资产 | 责任 | 事实源或入口 |
| --- | --- | --- |
| Domain Agent / Domain Graph | 承载一个业务域的状态、路由和结果契约 | backend/domains/__init__.py、域内注册模块与图构建器 |
| Capability | 表达可路由、可调度的业务能力及其治理元数据 | backend/orchestration/router/capabilities.yaml |
| Skill | 组合业务规则并在 Runtime 中执行 Capability | backend/skills/、backend/skills/registry.py |
| Tool | 封装单一外部操作或原子动作 | backend/tools/、backend/tools/tool_registry.py |
| Workflow | 执行预定义的多步流程 | backend/orchestration/workflows/ |
| MCP | 暴露平台 Tool，并消费外部 MCP 数据源 | mcp_servers/servers/、mcp_servers/protocol_app.py、backend/app/api/routes/mcp.py；外部客户端为 backend/infra/mcp_client.py |

依赖方向遵循现有分层。Tool 不承载跨业务流程；MCP 不复制 Tool 的业务实现；新增资产优先扩展现有注册和路由机制。

## 新增或修改流程

1. 先查找现有 Skill、Tool、Domain Graph、Workflow 与契约，确认没有重复能力。
2. Tool 保持原子职责，定义明确参数与结果，按现有方式注册；不要把 Tool 放在 Skill 目录，也不要从编排代码绕过 Tool 执行入口。
3. Skill 复用 BaseSkill 和当前能力执行适配器。修改参数、输出类型或 Capability 时，同步核对调用方和实际校验逻辑。
4. Capability 及其风险、操作、参数 Schema 和确认策略维护在现有 manifest；派生注册表和 lock 文件通过仓库生成器更新。
5. Workflow 复用现有 workflow registry；Domain Graph 使用现有 Domain Registry 并遵守 Router 预过滤接线；不要在主图 builder 手写自动发现节点。
6. 平台 MCP Server 在 mcp_servers/servers/ 实现并由 register_all() 登记，启动入口见 backend/app/server.py；参数 Schema 从 Tool 派生，适配器见 mcp_servers/schema_adapter.py。外部 MCP 消费走 backend/infra/mcp_client.py。两条通路都复用既有 Tool、鉴权和错误契约，不创建第二套 Tool 注册表。
7. 为新增或变化的失败、拒绝、降级和输出行为添加针对性断言，选择范围见 testing-guide.md。

## Tool 与 Skill 输出契约

每个 Skill 都要显式声明 `text` 或 `structured` 输出类型，并与其实际 Tool 结果保持一致：

- `text` 用于 Markdown/纯文本结果，直接作为模型可读内容传递，不包装成 JSON。
- `structured` 用于程序消费的 JSON 结果。成功封套为 `{"status":"success","data":...}`，失败封套为 `{"status":"failed","error":...}`。
- Skill/Workflow 边界负责解包和失败判定。成功只向 Reporter 传业务数据；失败必须保留失败语义，不能作为成功内容展示。

封套解析统一使用 `backend/shared/tool_envelope.py`；Skill 与 Workflow 的边界分别见 `backend/skills/base.py`、`backend/orchestration/workflow/skill_adapter.py`。契约守卫见 `backend/tests/skills/test_output_type_declarations.py` 和 `backend/tests/skills/test_tool_contract_boundary.py`。

## Tool Governance 与副作用

生产 Tool 执行必须经过当前 Governance Runtime 与安全执行器。Schema 校验、Capability/Domain 适用范围、请求预算、限流/熔断、去重以及用户确认由现有治理链负责；不得通过直接调用函数、模型输出或 MCP 路径绕过。

涉及写操作时，遵循当前操作预览、确认/审批和幂等契约。用户、租户和操作者身份只取自服务端验证后的请求上下文。任何 Tool 不得把授权、审批或副作用保护交给模型自行判断。

## 数据、模型与错误处理

- 数据访问执行资源级授权和租户隔离；SQL 查询遵循 SQL 域安全规范。
- Prompt 发布版本、模型调用和 Token/成本归因使用现有统一链路。
- 外部系统超时、拒绝、空结果和部分成功按既有 Tool/业务契约表达，不伪造成功、不静默吞错。
- 日志、Trace 与返回内容遵循敏感数据保护规则。

## 验证入口

先读 backend/tests/ 中最接近的现行测试，再选择单元、契约、接口或必要集成测试。共享注册或治理变化应覆盖其主要调用方。文档中的路径和命令如与代码不符，应以实现为准并同步修订本文件。
