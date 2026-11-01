# 全平台横切开发契约

> 覆盖 REST/SSE/WS、数据、状态、LLM、异步、前端、观测。是统一**行为底线**，不是新增通用业务 Schema。精确字段与迁移以对应源码为事实源，禁止根据文档凭空新增通用字段/表。

## 1. API / 前端 / 会话

- API 的入口必须声明可信身份、输入/输出 DTO、资源级权限、租户、幂等/版本约束和错误语义；422/401/403/404/409 与业务失败需真实区分。所有 UI（`frontend`、`frontend-admin`、`frontend-cs`）消费同一已发布接口契约，不能用前端成功 toast 掩盖后端失败。
- SSE 的开始、增量、工具进度、终态和断线重放以 [Frozen Contracts](../architecture/Frozen-Contracts.md) 及现有 `event_schema.py` 为准；不得随意加字段后只更新一个前端。消费端执行有序去重，断流后按真实可恢复范围提示，不将残缺答案标为成功。
- 客服实时事件（WS `cs:events` 封套 `{type,event_id,seq,ts}`，seq 游标补发）**只追加不破坏**：既有 message.created/agent_offered/handoff 状态事件之上，cs-workstation-v2 追加 `agent.status_changed`（瞬态不落库）、`conversation.transfer_requested/reassigned/offered`、`sla.warning`/`sla.breach`（幂等，SETNX/终态一次性）、`assist.suggestion`（瞬态）；人工接管后的用户消息经 `POST /cs/conversations/my/{id}/messages` 产生与图 turn 同构的 `message.created`，写通道收口全局幂等（Idempotency-Key 必填，同键同内容重放返回首次结果）。
- 普通问答不得产生隐式写入；命中“修改/退款/预订”先区分草案与正式操作。对于使用自动保存的 V2 正式行程，以领域修订版 CAS 校验成功后生效且可恢复；不能把历史 V1 Active/Draft 生命周期套到 V2。
- 前端可显示 Tool 进度、来源、失败限制、局部差异、确认按钮；但前端不是授权、状态或版本权威。后台任务的运行中、部分成功、失败、等待确认须与服务端一致。

## 2. 数据 / 事务 / 队列

- 业务表、版本迁移和多个 PostgreSQL 数据库目标参见 [数据库规范](../DATABASE.md)。所有读写在服务端绑定 tenant/user/资源作用域；SQL Agent 使用参数化、白名单、行级约束、只读权限。数据迁移先做备份、isolated schema 验证及回滚评估。
- Redis 热状态、缓存、锁、Broker、Checkpoint 不能替代权威业务实体。任务投递与业务执行需要明确 ack、recovery、timeout、重放、幂等及 UNKNOWN 状态；持久写失败不得回退为静默成功。
- 状态变更必须满足事务/并发竞争约束；跨多个实体和远程 Provider 不假设分布式原子事务。关键副作用须确认和可审计幂等，未知结果查账本；参见 [幂等手册](../operations/idempotency-runbook.md)。

## 3. 模型 / Agent / Tool / Provider

- Router 选择**归宿**而非越权；模型选择工具和填参数也必须经过候选白名单、域约束、Schema、用户授权和 Tool Governance。模型自报置信度不替代校准分数或安全检查。
- Prompt 版本、模型调用、用量/成本、Token 预算和上下文压缩使用平台统一代理、治理和观测入口；改变模型/Prompt 时记录版本及请求归因，不因省 token 省略必要证据或权限判断。
- 原子 Tool、组合 Skill、多步 Workflow、Domain Agent/Graph 与外部 MCP 的边界见 [Tool/Skill 规范](tool-skill-guide.md)。完整错误矩阵见 [失败/降级规范](../architecture/failure-degradation.md)：HTTP 200 可业务失败，内部 BUG 不变外部故障，硬依赖不假成功。
- RAG 的引用、证据门、索引生命周期；SQL 的只读/租户范围；客服的权限/交接/审批；旅游的 Trip Revision/Provider 实时核验必须各自遵循 [领域文档](../README.md#业务域)。

## 4. 安全 / 可观测 / 性能

- AuthN、RBAC、资源级 AuthZ、租户隔离是不同校验，失败关闭；不能信任前端传的身份、租户或 X-API-Key 当用户凭证。详见 [认证规范](../auth/README.md)。
- Trace/Logs/Metrics 保留 `trace_id`、路由理由、节点/Tool 状态、耗时、失败分类、来源/版本及必要模型成本归因；敏感值按 [数据保护](../security/llm-data-handling.md) 和 [Trace 规范](../observability/trace-model.md) 约束。Prometheus label 禁止高基数字段和 PII。
- 优化前必须提供 p50/p95、请求总耗时、工具耗时/重试次数、模型 token/成本和已证实瓶颈；异步/缓存/批量化不能改变结果语义和隔离边界。

## 5. 最低变更验收（和已有测试配合）

每个跨层修改至少覆盖：正常、鉴权拒绝、用户硬条件、空/脏数据、Tool/模型不可用、并发 CAS、部分结果/写超时、SSE/UI 终态、Trace 归因；只选择实际受影响的项，按 [T0–T3 测试策略](testing-guide.md) 执行，不要求每次跑全量。变更造成错误语义或 schema 改动时所有调用方必须同步，不以 README 声明代替测试。
