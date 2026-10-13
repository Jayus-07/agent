# Agent Platform 总改造计划（P0–P7）

> 状态：**经用户确认的目标路线图 / 不代表功能已实现**。2026-10-13 将 Runtime Control Center 作为 **P1-G** 正式纳入。具体施工验收以源代码、已合并 PR、测试和真实运行证据为准。
>
> 核心目标：一套 Agent Platform 底座，支持 **求职技术作品** 与 **跨境电商 AI 客服商业化验证** 两条交付路线。

## 施工主线与并行专项

| 阶段 | 范围 | 依赖 / 完成标准 |
| --- | --- | --- |
| P0 | 架构、协作、文档、UI、测试规范统一 | 主体规范建成；后续以现行规范及源码事实为准 |
| P1 | 公共 Runtime + 安全 + **P1-G 管理端** | 关闭安全 P0、统一错误/计费/发布/Trace；管理端真实业务状态与 API 对账 |
| P2 | 主助手 Conversation-first + Agent 职责收口 | 简单输入自然交流；需要时用 RAG/SQL/Tool/Planner；跨轮、独立域授权、真实 SSE 验收 |
| P3 | RAG、SQL、Memory 专项 | 检索证据、SQL 只读与资源权限、会话与长期记忆的一致性和实测 |
| P4 | Travel V2 | 对话规划、Provider、Trip 版本/编辑/恢复、失败状态和 PC/手机真实闭环 |
| P5 | 客服/选品/电商 | 先收口客服安全与状态，再验证 AI 售后 MVP；选品能力按市场反馈扩展 |
| P6 | Evaluation、Billing、Observability、运维 | 横向建设，供 P1–P5 复用；模型费用、Trace、评测及告警证据完整 |
| P7 | 全平台真实验收、部署、求职演示 | 安全 P0 全绿、真实业务 T3；简历、架构图、演示、案例和商业试点指标 |

**关键路径**：P1 公共 Runtime 安全交接 → P2 Conversation-first → P3 核心能力集成 → P7。P4/P5/P6 不改共享契约时可以并行，但不可抢占共享 Runtime 改动。各阶段按 T0–T3 选择风险匹配的验证。

## P1 拆分与 P1-G 新增决策

| 子任务 | 职责 | 与 P1-G 关系 |
| --- | --- | --- |
| B1-A | 异步运行与阻塞治理 | 向管理端暴露真实并发/阻塞指标 |
| B1-B | 模型调用、预算与费用归因 | 管理端读成本账本与受控预算操作 |
| B1-C | Workflow Deadline、取消、任务终态 | 管理端显示运行状态、耗时及终态 |
| B1-D | Prompt 发布、热更新、回滚 | 管理端按现有发布状态机接入版本治理 |
| B1-E | 按当前 Runtime 台账收口剩余项 | 不能据此文档假定已完工；需最新证据 |
| B1-F | 取消/超时/错误语义 | 管理端准确区分 HTTP、Tool、业务和 Run 状态 |
| **P1-G（新增）** | **Runtime Control Center 管理端** | **先冻结 UI/交互与契约，再复用/适配后端，最后接真实写操作与 T3 验收** |

P1-G 目标和批次详见 [Runtime Control Center 施工计划](../p1-runtime-control-center/TASK.md)。它是 P1 的**产品交付专项**，**不替代 B1-A～B1-F、不新增 Runtime 引擎、Router、账本或平行 Tool Governance**。已有 P1 在途 PR 优先完成安全交接，UI 可独立开发只读契约，但不能伪报真实后端接通。

## P2 批次（维持已确认范围）

- P2-0：Agent / Supervisor / Rule Node / Workflow / Skill / Tool 职责盘点与不必要 LLM 调用审查。
- P2-1：Conversation-first + Triage + 服务端可信能力范围。
- P2-2：多轮上下文与现有动态 DAG、Send、Agent 协作的真实验收。
- P2-3：SSE/Memory/History/Trace、权限、成本、质量体验收。

**当前不新增**执行中通用 Replanning、第二套 Workflow Engine、全域自动 Handoff、全 Tool MCP 迁移、灰度和影子双跑。已有 DAG、Supervisor、Workflow Runtime 继续复用。

## 双轨交付标准

- **求职线**：企业主助手、RAG/SQL、智能客服 3 个可解释且可复现的案例；有入口→Runtime→Tool→业务结果/Trace 架构图及有效验收证据。
- **商业线**：跨境电商售后业务优先访谈客户、确定最小需求、授权试点、记录成功率/人工处理时间/业务错误/单位成本，验证付费而非继续堆 Agent。

## 交付与项目管理

- 每批通过独立 PR、明确负责人和状态；文档设计完成 ≠ 代码合并 ≠ T3 验收。
- 实际进度记于 [PROGRESS.md](PROGRESS.md)。子任务可维护自己的 TASK/PROGRESS，不把动态 PR 列表复制到永久架构规范。
- 遵循 [现行文档规范](../../development/documentation-standard.md)、[测试策略](../../development/testing-guide.md)、[模块边界](../../architecture/module-boundaries.md)。
