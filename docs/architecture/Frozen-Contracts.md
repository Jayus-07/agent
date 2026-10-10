# 公共契约与守卫入口

本文只记录跨模块调用方依赖的稳定契约和源码入口。具体字段、枚举和运行参数以当前 schema、manifest、迁移和实现为准；本文件不维护动态资产数量。

| 契约面 | 必须保持的行为 | 主要事实源 |
| --- | --- | --- |
| Router 决策 | 运行目标使用统一决策类型；兼容 state 字段只由现有投影生成，不在节点内各自写入 | backend/orchestration/router/、backend/orchestration/state_projection.py |
| 主图与域图 | 主图内置节点由 builder 管理；Skill 与 Domain Graph 按当前 registry 发现；新增入口核对预过滤和注册描述 | backend/orchestration/graph/builder.py、backend/domains/、backend/orchestration/domain_registry.py |
| Capability 与 Tool | Capability 声明集中于 capabilities.yaml；参数 schema、风险和确认策略经当前 Tool Governance 执行 | backend/orchestration/router/capabilities.yaml、backend/core/tool_governance/、backend/skills/base.py |
| Tool 结果 | 执行结果、业务结果、失败与降级分别表达；副作用操作经过确认/审批和幂等链路 | backend/core/tool_governance/、backend/core/tool_runtime/ |
| 身份与数据范围 | 只信任服务端验证的身份；读写执行资源级授权和租户隔离；不可验证授权范围时失败关闭 | backend/security/、backend/sql/policy.py、各域 repository |
| SQL | 只读执行；授权表范围和数据 scope 来自服务端策略；生成 SQL 经过 Validator 与受限执行器 | backend/sql/policy.py、backend/sql/sql_validator.py、backend/sql/executor.py |
| SSE 与事件 | 事件类型、必需字段和状态顺序以 schema 与 API 实现为准；契约变化同步检查所有客户端消费方 | backend/orchestration/graph/event_schema.py、backend/app/api/routes/chat.py |
| SSE 断线恢复 | 事件按 `(stream_id, seq)` 有序编号并采用 at-least-once 重放；客户端按序号去重。重放与实时订阅原子衔接；缓冲已产生缺口或进程重启后不可恢复时，必须明确失败，不能跳帧续播 | backend/app/api/stream_resume.py、backend/tests/api/test_sse_resume.py |
| Checkpoint | 状态键、线程标识和恢复范围按用户、租户、会话隔离；只读流程不得覆盖可写状态 | backend/orchestration/、backend/travel/graph_builder.py、各域存储层 |
| Prompt 与计费 | 模型调用、Prompt 发布版本、用量和成本由现有统一代理及计费入口处理 | backend/infra/llm/、backend/prompts/ |
| Schema 与迁移 | 数据结构变化通过已有迁移机制；不手工并行维护派生 lock 或契约快照 | backend/sql/migrations/ 与对应生成器 |

## 变更流程

公共字段、事件、路由状态、授权或持久化语义变化时：

1. 先找出当前 schema、服务端实现和所有调用方。
2. 为成功、拒绝、失败和恢复路径补充有识别力的断言。
3. 同步兼容升级、迁移和客户端消费约束。
4. 按风险使用 [测试策略](../development/testing-guide.md) 选择 T0–T3 验证。

断线恢复额外约束：恢复接口必须校验用户与租户身份；ping 仅保活，不进入可重放事件序列。进程内缓冲、完成记录 TTL 和缺口行为以 `stream_resume.py` 与测试为准；服务重启后不得假称旧流仍可恢复。
