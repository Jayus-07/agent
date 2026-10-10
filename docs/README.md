# 文档导航

本目录只提供现行文档入口，不维护项目规模统计、历史报告索引或第二份架构说明。文档与代码不一致时，以当前实现、配置、迁移和测试为依据。

## 核心文档

| 文档 | 职责 | 按需读取 |
| --- | --- | --- |
| [architecture/system-overview.md](architecture/system-overview.md) | 部署边界、服务关系、异步与网关 | 修改部署、网关或异步运行时 |
| [architecture/ai-runtime.md](architecture/ai-runtime.md) | 主图、域图接入、公共运行状态契约 | 修改 Router、LangGraph、SSE 或 Checkpoint |
| [architecture/domain-service-map.md](architecture/domain-service-map.md) | 域服务、Provider、凭据和失败边界 | 新增或调整外部服务 |
| [architecture/Frozen-Contracts.md](architecture/Frozen-Contracts.md) | 当前公共契约和守卫入口 | 修改 SSE、Runtime、Tool 或 Domain 契约 |
| [development/tool-skill-guide.md](development/tool-skill-guide.md) | Agent、Skill、Tool、Workflow、MCP 开发规范 | 新增或修改上述资产 |
| [development/testing-guide.md](development/testing-guide.md) | 唯一详细测试策略，T0–T3 | 选择或升级验证范围 |
| [operations/commands.md](operations/commands.md) | 经核对的常用启动与验证命令 | 启停服务或调试环境 |
| [operations/customer-service-runbook.md](operations/customer-service-runbook.md) | 客服 FAQ、RAG 降级和告警响应 | 运维客服问答链路 |
| [operations/idempotency-runbook.md](operations/idempotency-runbook.md) | 副作用幂等账本与不确定交易的人工处置 | 处理副作用状态或账本异常 |
| [auth/README.md](auth/README.md) | 网关身份、后端身份解析与授权边界 | 修改登录、身份头、会话或 RBAC |
| [security/llm-data-handling.md](security/llm-data-handling.md) | 文档内容发送模型前的数据边界与合规核查项 | 修改文档解析、模型出口或 Trace 留存 |

## 业务域

- [旅游](domains/travel.md)：对话规划、行程版本和旅游 V2 API。
- [客服](domains/customer-service.md)：客服域图、状态流转和人工坐席边界。
- [RAG](domains/rag.md)：文档索引、检索、证据门和降级。
- [SQL](domains/sql.md)：NL2SQL 执行链、安全校验和数据范围。

## 源码入口

- API 装配：backend/app/api/router.py
- 主图：backend/orchestration/graph/builder.py
- Domain Graph 注册：backend/domains/__init__.py
- Capability 与 Tool 治理声明：backend/orchestration/router/capabilities.yaml
- 测试：backend/tests/

## 维护方式

- 按任务阅读，不要求通读本目录。
- 规范和契约各保留一个主要入口；兼容指针只链接权威文档，不复制内容。
- 长任务在 docs/tasks/<task-id>/ 保存 TASK.md 和 PROGRESS.md；简单任务不建档。
- 历史审计、交接、施工和验收报告不进入本导航。
