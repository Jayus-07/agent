# AGENTS.md

> 仓库级协作规则与按需文档导航。动态功能现状、数量和运行参数以代码、配置和迁移为准。

## 项目说明

本项目是基于 FastAPI、LangGraph、Next.js、PostgreSQL、Redis 和 APISIX 的 Agent Platform。
业务能力包括 RAG、SQL Agent、智能客服、旅游规划和选品。

## 协作原则

- 面向用户的输出一律用中文，包括过程叙述、进度说明和结论；代码、命令、标识符和必要术语保持原文。
- 执行任务遵循马斯克五步骤工作流，顺序不可逆：

  1. **质疑需求**：动手前先质疑需求是否必要、是否正确，有疑义先澄清再执行；先检查工作区与相关代码再确定改动范围；保留其他会话的修改。
  2. **删除**：优先删除多余的代码、流程与依赖；复用既有路由、Runtime、注册表、契约和审批机制，不建立平行实现。
  3. **简化和优化**：删除之后再简化，只优化该存在的东西；Bug 先定位根因，不顺手重构、不改变未要求的业务行为。
  4. **加快周期**：先做对再提速，正确性未验证前不为效率牺牲质量。
  5. **自动化**：放在最后；为架构、公共契约或关键行为变化补充能识别回归的验证，让正确性可持续。
- 按风险和依赖范围选择最小充分测试；安全、权限、状态和公共契约变化必须覆盖对应边界。
- 详细测试分层统一见 testing-guide.md，使用 T0–T3。
- 不以扩大 mock、删断言、skip 或 xfail 掩盖失败。
- 不执行默认全量回归；测试选择遵循 testing-guide.md。
- 开发与测试优先复用本机依赖及 Docker BuildKit 缓存；依赖输入未变时不重复安装浏览器或重建依赖层。
- 未实际执行的验证不得报告为通过。

## 架构与安全红线

- 沿用 Router / Runtime / Domain Agent / Skill / Tool 的职责边界；MCP 是接入适配方式。
- `/agent` 主助手与 Travel/Customer Service 为不同业务入口；跨产品能力必须按服务端可信身份/权限过滤，`domain_hint` 不是授权依据。Conversation-first 目标见 [ADR-0002](docs/architecture/adr/0002-main-agent-conversation-first.md)（已决策、待施工；不得宣称已实现）。
- 已有 registry、manifest、schema、迁移、生成器或发布指针时，以其作为唯一事实源。
- 身份只信任服务端验证后的上下文；资源读写执行资源级授权和租户隔离。
- SQL 使用参数化、只读权限、授权表范围和现有安全校验；不得绕过或降级为宽权限执行。
- 有副作用的 Tool 经过现有 Governance、确认/审批和幂等机制。
- LLM、Prompt 版本、用量和计费使用统一链路，不在调用点重复实现。
- Tool/Provider 失败先区分传输、执行、业务结果及任务终态；按当前用户目标动态判硬依赖，执行 [失败与降级规范](docs/architecture/failure-degradation.md)，不得吞异常、伪造成功或静默丢任务。
- 关键链路保留必要的结构化日志与 Trace；敏感数据不得写入日志。

## 本地 Docker 运行纪律

- 本机共享栈 = 仓库根 docker-compose.yml 叠加 部署/prod-4c8g/docker-compose.local.yml（compose 项目名 agent）。`ragu1-*` 为隔离测试栈、`agent-gh-runner` 为 CI、observability 全家桶属 profile 储备，均不属日常栈，禁擅动。
- 改后端代码唯一生效路径 = `devctl.bat rebuild`（compose --build 重建）；start/restart 不换代码、不换 .env。
- 一键启停用 `devctl.bat start / stop`（含 apisix 与全部基础设施）；不要使用 `--remove-orphans` 或 `down -v` 清理共享栈。
- 端口 8000 / 8090 / 8091 / 9080 / 5433 由主栈占用；并行会话做验证时用错位端口与独立容器名，不与主栈抢绑定。
- worktree 默认没有 .env（gitignore）：compose 或依赖 SSRF_FAKEIP_AWARE 等开关的测试需要时，从主仓库拷贝。
- 命令与排障细节见 [commands.md](docs/operations/commands.md)；生产与本机 4C8G 资源档位见 [部署/prod-4c8g/README.md](部署/prod-4c8g/README.md)。

## 按需读取

| 任务                                         | 现行文档                                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 网关、部署、异步运行                         | [system-overview.md](docs/architecture/system-overview.md)                                                                       |
| Router、LangGraph、Runtime、SSE、Checkpoint  | [ai-runtime.md](docs/architecture/ai-runtime.md)                                                                                 |
| `/agent` 对话优先目标架构与跨产品隔离         | [ADR-0002：Conversation-first](docs/architecture/adr/0002-main-agent-conversation-first.md)（待施工）                           |
| Provider、凭据和跨域服务关系                 | [domain-service-map.md](docs/architecture/domain-service-map.md)                                                                 |
| 新增或修改 Agent、Skill、Tool、Workflow、MCP | [tool-skill-guide.md](docs/development/tool-skill-guide.md)                                                                      |
| Tool/Provider 异常、失败分级与降级           | [failure-degradation.md](docs/architecture/failure-degradation.md)                                                                 |
| 文档标准、同步责任与交付                    | [documentation-standard.md](docs/development/documentation-standard.md)                                                               |
| 全平台模块边界与目录归属                 | [module-boundaries.md](docs/architecture/module-boundaries.md)、[repository-layout.md](docs/development/repository-layout.md) |
| 跨层 API、状态、安全和可观测契约           | [cross-cutting-contracts.md](docs/development/cross-cutting-contracts.md) |
| 全仓审计与安全重构                      | [refactoring-playbook.md](docs/development/refactoring-playbook.md) |
| 发布质量与上线门禁                      | [release-quality.md](docs/operations/release-quality.md) |
| CI 与评测环境规范                        | [ci-evaluation-environment.md](docs/operations/ci-evaluation-environment.md) |
| 三端前端、SSE、状态与共享组件               | [frontend-guide.md](docs/development/frontend-guide.md) |
| 三端 UI/UX 设计、响应式与页面一致性 | [frontend-design-standard.md](docs/development/frontend-design-standard.md) |
| 模型预算/价格/计费/对账                    | [llm-budget-billing.md](docs/governance/llm-budget-billing.md) |
| Trace 与 Span、脱敏和版本归因               | [trace-model.md](docs/observability/trace-model.md) |
| Prometheus / Grafana 和告警               | [grafana-guide.md](docs/observability/grafana-guide.md) |
| Prompt 发布、热更新及回滚                    | [prompt/architecture.md](docs/prompt/architecture.md) |
| 供应商模型登记/角色绑定/DB 热更新          | [model-provider-runtime.md](docs/governance/model-provider-runtime.md) |
| RBAC/IDOR/租户/资源级授权                 | [authorization-standard.md](docs/auth/authorization-standard.md) |
| RAG 上传、切分、索引、检索和证据             | [rag.md](docs/domains/rag.md) |
| 各功能评测、Golden、Release Gate          | [evaluation-governance.md](docs/evaluation/evaluation-governance.md) |
| API/SSE/WS 兼容与恢复 | [api-event-lifecycle.md](docs/contracts/api-event-lifecycle.md) |
| Celery/队列/Lease/重试与幂等 | [async-task-governance.md](docs/architecture/async-task-governance.md) |
| 业务数据版本、删除、备份与迁移 | [data-lifecycle.md](docs/governance/data-lifecycle.md) |
| L1/L2/L3 长期记忆和画像更新 | [memory-governance.md](docs/governance/memory-governance.md) |
| 选品/电商领域的目标与现状 | [selection-ecommerce.md](docs/domains/selection-ecommerce.md) |
| 测试策略                                     | [testing-guide.md](docs/development/testing-guide.md)                                                                            |
| 常用启动、测试和调试命令                     | [commands.md](docs/operations/commands.md)                                                                                       |
| 生产/本机 4C8G 部署、资源档位与启动清单 | [部署/prod-4c8g/README.md](部署/prod-4c8g/README.md) |
| 旅游、客服、RAG、SQL 业务                    | [旅游](docs/domains/travel.md)、[客服](docs/domains/customer-service.md)、[RAG](docs/domains/rag.md)、[SQL](docs/domains/sql.md) |
| 旅游 V2 产品设计、审计与前端施工计划 | [product-design.md](docs/domains/travel-v2/product-design.md)、[fe0-audit.md](docs/domains/travel-v2/fe0-audit.md)、[implementation-plan.md](docs/domains/travel-v2/implementation-plan.md) |

普通 Bug 优先阅读相关源码与测试。只阅读当前任务需要的文档，不要求加载整个 README 或 docs；历史报告不作为默认上下文。

## 长任务续工

- 简单任务不创建任务文件；跨阶段或跨会话任务使用 docs/tasks/<task-id>/TASK.md 与 PROGRESS.md。
- TASK 记录目标、范围和验收；PROGRESS 记录证据、阻塞和下一步。
- 恢复任务时核对任务文件、Git 状态、代码和测试；无证据不标记完成。
- 临时事项处理后回到原计划；用户改变、暂停或取消目标时遵从最新要求。
- 暂停/取消/交接区分 `PAUSED`（预计续工）/`BLOCKED`（外部阻塞）/`CANCELLED`（用户取消）；取消不撤销已提交的副作用，暂停时不启动新改动或新测试。
- 暂停时记录任务 ID、worktree、branch、HEAD、已改文件、未提交状态、测试证据、阻塞与下一步；不擅自 stash/reset/清理/合并。
- 恢复前重读最新指令与任务文件，核对 worktree、HEAD、未提交修改与上游变更；有冲突先报告，不覆盖其他会话成果。
- 完整契约与运行时生命周期边界见 [Agent 任务暂停与生命周期设计稿](docs/plans/agent-task-pause-resume-lifecycle.md)。

## 文档维护与交付

- 架构、接口、数据状态或开发契约变化时，更新受影响的现行文档；普通内部修复无需例行写报告。
- 文档与代码冲突时，核实当前代码、配置、迁移和测试后再修正文档。
- 文档移动或删除前检查 Git 跟踪状态、引用、工具依赖和是否属于在途任务。
- 交付时简述改动、实际验证结果、文档同步情况及未完成风险。

## Git 并行开发规范

1. 每个独立开发任务使用独立 Git Worktree 和任务分支，优先使用 AI 工具内置 Worktree 功能；没有内置功能时使用 Git 原生命令。
2. 新任务从已提交的 main 创建工作树，同一任务跨会话继续时复用原工作树。
3. 修改代码前确认实际工作目录；不得直接修改 main 或其他任务工作树。如果无法切换到目标目录，停止修改并报告。
4. 开发结束后提交任务分支，输出 Commit SHA、测试结果和未完成事项；未经确认不得合并 main。
5. 合并后才允许清理工作树；未提交修改、未合并提交、忽略文件和历史记录必须先检查并保留。
6. 禁止强制清理、覆盖其他任务修改或操作共享数据库；涉及冲突时先报告。

原则：**独立任务、独立工作树、统一合并、安全清理。**

AI 使用内置 Worktree 功能或执行 `git worktree add`、`git merge` 等 Git 原生命令；用户不必自己输入命令，也不必维护额外脚本。
