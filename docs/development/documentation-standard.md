# 现行文档维护与交付标准

> 面向 Codex、Zcode 和人工维护者的仓库文档约束。**精简导航、单一事实源、修改时同步、可验证**。不增加每次 Bug 修复的文档负担。

## 1. 文档职责与权威顺序

| 文档位置 | 内容 | 不应该写 |
| --- | --- | --- |
| `AGENTS.md` | 简短协作红线与按需导航 | 完整架构、动态资产清单、重复验收规范 |
| `docs/README.md` | 现行规范的导航索引 | 第二份系统全景/长报告 |
| `docs/architecture/` | 系统拓扑、跨模块契约、失败策略、ADR | 每日端口/工具数量等动态清单 |
| `docs/development/` | Agent/Skill/Tool 开发与测试规则、本文 | 与源码无关的理想化操作步骤 |
| `docs/domains/` | 领域边界、状态机、实现入口 | 复制跨域治理协议 |
| `docs/operations/` | 已核对的启动、排障、恢复手册 | 没有验证的危险生产命令 |
| `docs/tasks/` 与 `docs/reports/` | 长任务进度和一次性验证证据 | 替代现行规范 |

事实源优先级：**实际代码/配置/Schema/迁移和通过的对应测试 → 现行跨域规范 → 领域说明 → 历史报告**。现有实现和目标规则冲突时**记录差距**，不要改写文档假称已实现，也不要以旧报告推翻当前代码。动态 Provider、Tool、Agent 数量及参数从注册表或实际配置读取。

跨领域专项主责：`development/frontend-guide.md` 管三端前端；`governance/llm-budget-billing.md` 管额度、费用与对账；`observability/trace-model.md` 管 Trace 契约；`observability/grafana-guide.md` 管看板/告警；`prompt/architecture.md` 管 Prompt 生命周期；`development/tool-skill-guide.md` 管 Capability/Skill/Tool/MCP。专项文件只记录各自规范，公共权限/失败/测试仍链接既有权威文档，禁止复制。

## 2. 变更触发与维护人职责

- 改 Router、Runtime、Agent/Skill/Tool、Tool Error/Fallback、身份、租户、SSE、数据模型时，同一变更更新**对应权威文档**和必要的守卫/契约测试；仅小型内部重构或普通 Bug、且公共行为不变时不强制修改文档。
- 新增规范先搜索是否已有相同主责文档：能增补就不新建；必须新增时在 `docs/README.md` 和 `AGENTS.md` 补**链接**，禁止复制整篇到多个入口。
- 行为设计和当前现状分段书写，标记“已实现/待实现/仅建议”。引入新的跨模块设计决策才写 `docs/architecture/adr/NNNN-*.md`：背景、决策、候选与取舍、兼容/回滚影响。
- 开发先定位源码入口及现有测试；不要通过“标准化”顺手改动未经批准的业务行为、数据或权限边界。
- 跨阶段/跨会话任务用 `docs/tasks/<task-id>/TASK.md` 和 `PROGRESS.md` 保存目标、范围、证据、下一步；单次小修不创建任务流水账。

## 3. 文档最小模板（主规范）

1. 目的与适用范围；区分规范和现状。
2. 职责边界：谁处理什么、不处理什么；必要时附简短 Mermaid 图。
3. 明确的不变量/禁止事项和失败处理；不要只画正常路径。
4. 事实源：对应代码、Schema、测试；使用仓库**相对链接**。
5. 变更清单与验收：可操作、可判定、可留下证据，不写“功能正常”空话。

内容风格：优先稳定概念与边界；避免复制大量函数、版本号、策略默认值、行数、依赖数量和环境路径。配置/字段经常变时只链接事实源。移动/删文件前先搜索全部引用和正在进行中的工作树任务。

更广的标准入口：[全平台模块边界](../architecture/module-boundaries.md)、[目录规范](repository-layout.md)、[横切契约](cross-cutting-contracts.md)、[安全重构](refactoring-playbook.md)、[质量与发布](../operations/release-quality.md)。本文仍是唯一文档维护规则。

## 4. 提交门禁与交付

- T0：Markdown 本地链接指向仓库现有文件、新文件互链正确，代码路径核对存在；无密钥/本机私人地址/失效示例命令；目录索引能找到规范。
- T1/T2：只有当变更同时修改实现、公共接口或安全/失败语义时，依 [testing-guide](testing-guide.md) 选择对应定向与契约测试；文档本身不假称运行全量集成。
- 交付说明写清：变更文件、基线、实际做了哪些检查、未验证范围、分支与 Commit/PR 链接、是否合入主线；“已推送 PR”不得写成“已合并生效”。
- 合并前检查 `main` 是否前进、是否影响其他工作树的已提交修改；不强推、不清理他人工作树、不覆写其他任务草稿。

## 5. 规范基线收口与后续维护

本轮规范完成后，除重大功能边界变化，不再集中新增概念相似的“标准文档”。**后续主要任务改为对照规范审查和整改代码**，新文档只能在已有主责无法承载且具备真实事实源时创建。现有专门职责入口还包括：

- `contracts/api-event-lifecycle.md`：REST、SSE、WS、错误及兼容；
- `architecture/async-task-governance.md`：Celery/QueueRouter/Admission/Lease/Retry/Recovery；
- `governance/data-lifecycle.md`：正式业务数据、发布、迁移、备份与恢复；
- `governance/memory-governance.md`：三层记忆、画像冲突、长期归因；
- `domains/selection-ecommerce.md`：选品和未来电商子域的已实现/目标区分。

`docs/ARCHITECTURE.md`、`docs/AGENT_DESIGN.md`、`docs/PRD.md` 等历史入口须通过审计确认使用范围，适用时加“历史设计/请读现行规范”指针；**未审计前不删除或大面积移动**，不要将旧版规范当作新需求实现依据。

下一步按 [安全重构手册](refactoring-playbook.md) 分批核实实际代码：权限/租户与数据生命周期 → API/Runtime/Tool/模型预算/记忆/评测 → 领域 RAG/SQL/客服/旅游/选品 → 前端共享与目录清洁。每一批保留 PASS/FAIL/INVALID/BLOCKED 等对应证据，不能以“文档已完成”代替上线验收。
