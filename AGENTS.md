# AGENTS.md

> 仓库级协作规则与按需文档导航。动态功能现状、数量和运行参数以代码、配置和迁移为准。

## 项目说明

基于 FastAPI、LangGraph、Next.js、PostgreSQL、Redis 和 APISIX 的 Agent Platform。
业务能力包括 RAG、SQL Agent、智能客服、旅游规划和选品。

## 协作原则

- 面向用户的输出一律用中文；代码、命令、标识符和必要术语保持原文。
- 遵循马斯克五步骤（顺序不可逆）：质疑需求 → 删除多余 → 简化优化 → 加快周期 → 自动化。
- 质疑需求：动手前确认需求必要且正确，有疑义先澄清；先检查工作区与相关代码再定改动范围；保留其他会话的修改。
- 删除与复用：优先删除多余的代码、流程与依赖；复用既有路由、Runtime、注册表、契约和审批机制，不建立平行实现。
- 简化：Bug 先定位根因，不顺手重构、不改变未要求的业务行为；正确性未验证前不为效率牺牲质量。
- 自动化放最后：为架构、公共契约或关键行为变化补充能识别回归的验证。
- 按风险和依赖范围选择最小充分测试（分层 T0–T3 见 docs/development/testing-guide.md）；安全、权限、状态和公共契约变化必须覆盖对应边界；不执行默认全量回归。
- 不以扩大 mock、删断言、skip 或 xfail 掩盖失败；未实际执行的验证不得报告为通过。
- 开发与测试优先复用本机依赖及 Docker BuildKit 缓存；依赖输入未变时不重复安装浏览器或重建依赖层。

## 架构与安全红线

- 沿用 Router / Runtime / Domain Agent / Skill / Tool 的职责边界；MCP 是接入适配方式。
- `/agent` 主助手与 Travel/Customer Service 为不同业务入口；跨产品能力必须按服务端可信身份/权限过滤，`domain_hint` 不是授权依据。Conversation-first 目标见 docs/architecture/adr/0002-main-agent-conversation-first.md（已决策、待施工；不得宣称已实现）。
- 已有 registry、manifest、schema、迁移、生成器或发布指针时，以其作为唯一事实源。
- 身份只信任服务端验证后的上下文；资源读写执行资源级授权和租户隔离。
- SQL 使用参数化、只读权限、授权表范围和现有安全校验；不得绕过或降级为宽权限执行。
- 有副作用的 Tool 经过现有 Governance、确认/审批和幂等机制。
- LLM、Prompt 版本、用量和计费使用统一链路，不在调用点重复实现。
- 模型分工：LLM 推理一律使用 Provider 登记的云端模型，不在本地下载或部署推理模型；嵌入/重排用本地专项模型服务 127.0.0.1:8088（容器内 host.docker.internal:8088），用法见 docs/operations/commands.md。
- Tool/Provider 失败先区分传输、执行、业务结果及任务终态；按当前用户目标动态判硬依赖，执行 docs/architecture/failure-degradation.md；不得吞异常、伪造成功或静默丢任务。
- 关键链路保留必要的结构化日志与 Trace；敏感数据不得写入日志。

## 本地 Docker 运行纪律

- 本机共享栈 = 根 docker-compose.yml 叠加 部署/prod-4c8g/docker-compose.local.yml（compose 项目名 agent）。`ragu1-*` 为隔离测试栈、`agent-gh-runner` 为 CI、observability 全家桶属 profile 储备，均不属日常栈，禁擅动。
- 改后端代码唯一生效路径 = `devctl.bat rebuild`（compose --build 重建）；start/restart 不换代码、不换 .env。
- 代码修改完成后，必须用本任务 Worktree 中的最新代码重建受影响的代码服务并确认健康；不得从含其他会话未提交改动的主目录构建。无法安全重建或健康检查失败时标记 `BLOCKED` / `UNVERIFIED`，说明原因和下一步，不得报告为已部署。
- 一键启停用 `devctl.bat start / stop`（含 apisix 与全部基础设施）；不用 `--remove-orphans` 或 `down -v` 清理共享栈。
- 开工先确认环境状态（`docker compose ls`、`docker stats --no-stream`），占用以容器运行状态为唯一事实，不按 Docker Desktop 显示的组数判断；健康检查命令见 docs/operations/commands.md。
- 默认复用共享栈，只启动当前任务缺少且必需的服务；宿主机可用内存低于 2 GB 时不新建第二套完整栈、不并行构建，必要时改用不依赖 Docker 的定向验证。
- 确需并行验证时，用独立 Compose project、错开端口和隔离数据资源只启动必需服务；验证完成后按 Compose project 名和工作目录确认归属，再停止本任务创建的隔离栈（保留状态用 `stop`，确认无需保留才用不带 `-v` 的 `down`）；共享栈不能按会话整体停删，禁止 `docker system prune`、`docker container prune`、`down -v`。
- 端口 8000 / 8090 / 8091 / 9080 / 5433 由主栈占用；并行验证用错位端口与独立容器名，不与主栈抢绑定。
- worktree 默认没有 .env（gitignore）：需要时从主仓库拷贝。
- 命令与排障见 docs/operations/commands.md；资源档位见 部署/prod-4c8g/README.md。

## 按需读取

只读当前任务需要的文档；普通 Bug 优先读相关源码与测试，历史报告不作为默认上下文。

**架构与运行时**

| 任务 | 现行文档 |
| --- | --- |
| 网关、部署、异步运行 | docs/architecture/system-overview.md |
| Router、LangGraph、Runtime、SSE、Checkpoint | docs/architecture/ai-runtime.md |
| /agent 对话优先目标架构与跨产品隔离 | docs/architecture/adr/0002-main-agent-conversation-first.md（待施工） |
| Provider、凭据和跨域服务关系 | docs/architecture/domain-service-map.md |
| Celery/队列/Lease/重试与幂等 | docs/architecture/async-task-governance.md |
| API/SSE/WS 兼容与恢复 | docs/contracts/api-event-lifecycle.md |

**开发与测试规范**

| 任务 | 现行文档 |
| --- | --- |
| 新增或修改 Agent、Skill、Tool、Workflow、MCP | docs/development/tool-skill-guide.md；动态 MCP 另见 docs/architecture/adr/0011-mcp-capability-center.md |
| Tool/Provider 异常、失败分级与降级 | docs/architecture/failure-degradation.md |
| 文档标准、同步责任与交付 | docs/development/documentation-standard.md |
| 全平台模块边界与目录归属 | docs/architecture/module-boundaries.md、docs/development/repository-layout.md |
| 跨层 API、状态、安全和可观测契约 | docs/development/cross-cutting-contracts.md |
| 全仓审计与安全重构 | docs/development/refactoring-playbook.md |
| 三端前端、SSE、状态与共享组件 | docs/development/frontend-guide.md |
| 三端 UI/UX 设计、响应式与页面一致性 | docs/development/frontend-design-standard.md |
| 测试策略 | docs/development/testing-guide.md |

**治理与安全**

| 任务 | 现行文档 |
| --- | --- |
| 模型预算/价格/计费/对账 | docs/governance/llm-budget-billing.md |
| 供应商模型登记/角色绑定/DB 热更新 | docs/governance/model-provider-runtime.md |
| RBAC/IDOR/租户/资源级授权 | docs/auth/authorization-standard.md |
| 业务数据版本、删除、备份与迁移 | docs/governance/data-lifecycle.md |
| L1/L2/L3 长期记忆和画像更新 | docs/governance/memory-governance.md |
| Prompt 发布、热更新及回滚 | docs/prompt/architecture.md |

**运维、评测与观测**

| 任务 | 现行文档 |
| --- | --- |
| 发布质量与上线门禁 | docs/operations/release-quality.md |
| CI 与评测环境规范 | docs/operations/ci-evaluation-environment.md |
| 各功能评测、Golden、Release Gate | docs/evaluation/evaluation-governance.md |
| Trace 与 Span、脱敏和版本归因 | docs/observability/trace-model.md |
| Prometheus / Grafana 和告警 | docs/observability/grafana-guide.md |
| 常用启动、测试和调试命令 | docs/operations/commands.md |
| 生产/本机 4C8G 部署、资源档位与启动清单 | 部署/prod-4c8g/README.md |

**业务域**

| 任务 | 现行文档 |
| --- | --- |
| RAG 上传、切分、索引、检索和证据 | docs/domains/rag.md |
| 旅游、客服、RAG、SQL 业务 | docs/domains/travel.md、docs/domains/customer-service.md、docs/domains/rag.md、docs/domains/sql.md |
| 旅游 V2 产品设计、审计与前端施工计划 | docs/domains/travel-v2/（product-design.md、fe0-audit.md、implementation-plan.md） |

## 长任务续工

- 简单任务不创建任务文件；跨阶段或跨会话任务使用 docs/tasks/<task-id>/TASK.md 与 PROGRESS.md（TASK 记录目标、范围和验收；PROGRESS 记录证据、阻塞和下一步）。恢复时核对任务文件、Git 状态、代码和测试；无证据不标记完成。
- 临时事项处理后回到原计划；用户改变、暂停或取消目标时遵从最新要求。
- 暂停/取消/交接区分 `PAUSED`（预计续工）/`BLOCKED`（外部阻塞）/`CANCELLED`（用户取消）；取消不撤销已提交的副作用，暂停期间不启动新改动或新测试。
- 暂停时记录任务 ID、worktree、branch、HEAD、已改文件、未提交状态、测试证据、阻塞与下一步；不擅自 stash/reset/清理/合并。
- 恢复前重读最新指令与任务文件，核对 worktree、HEAD、未提交修改与上游变更；有冲突先报告，不覆盖其他会话成果。
- 完整契约与运行时生命周期边界见 docs/plans/agent-task-pause-resume-lifecycle.md。

## 文档维护与交付

- 架构、接口、数据状态或开发契约变化时，更新受影响的现行文档；普通内部修复无需例行写报告。
- 文档与代码冲突时，核实当前代码、配置、迁移和测试后再修正文档。
- 文档移动或删除前检查 Git 跟踪状态、引用、工具依赖和是否属于在途任务。
- 交付时简述改动、实际验证结果、文档同步情况及未完成风险。

## Git 并行开发规范

1. 每个独立开发任务使用独立 Git Worktree 和任务分支（优先 AI 工具内置 Worktree 功能，否则用 git 原生命令）；新任务从已提交的 main 创建，同一任务跨会话复用原工作树。
2. 修改代码前确认实际工作目录；不得直接修改 main 或其他任务工作树；无法切换到目标目录时停止修改并报告。
3. 完成实现后，先完成代码重建和验证，再展示 Diff、验证结果及未完成事项并询问是否提交；收到明确确认后才提交任务分支并输出 Commit SHA；未获确认保持未提交，未经确认不得合并 main。
4. 合并后才允许清理工作树；未提交修改、未合并提交、忽略文件和历史记录必须先检查并保留。
5. 禁止强制清理、覆盖其他任务修改或操作共享数据库；涉及冲突先报告。
6. 所有改动一律走 PR 合并 main（含自己开分支自己合），让 PR 预检覆盖 100% 变更（历史盘点：绕过 PR 的直推是测试/CI 漂移的主要入口）。
7. 新功能/链路环节合入时，同步把对应测试加进 rag_smoke 定向清单（.github/workflows/rag_smoke.yml），并遵守「改常量→同步守卫断言、删模块→删测试、加依赖→重新 lock、换评测数据→对齐 CI 引用」；全量健康度由每周 Full Regression workflow 监控（失败告警不挡合并）。
8. **工作树收尾必须有可复核结果**：任务开始和准备结束时都运行 `scripts/worktree_lifecycle.ps1 -Action Audit -RefreshRemote`，按输出逐棵区分主工作树、仍有改动、未证明合并、已合并且干净。PR 合并后，仅在改动与忽略文件已检查、证据已保留、确认无人使用时才运行 `-Action Remove -Path <工作树路径> -Apply -ConfirmInactive`；仅当 Git 能安全确认已合并时才加 `-DeleteBranch`，禁止强删。删除后运行 `-Action VerifyCloseout -Path <已删除路径>`；用户要求只留 main 时额外加 `-RequireOnlyMain`。检查未通过报 `BLOCKED` 并列明原因，不得写成已收尾；脚本默认只盘点，不自动删除、归档证据或同步 main。

原则：**独立任务、独立工作树、统一合并、安全清理。**

AI 使用内置 Worktree 功能或 Git 原生命令创建工作树；任务开始和收尾使用仓库维护的 `scripts/worktree_lifecycle.ps1` 留下检查结果，用户不必自己输入命令。
