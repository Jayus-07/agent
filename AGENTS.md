# AGENTS.md

> 仓库级协作规则与按需文档导航。动态功能现状、数量和运行参数以代码、配置和迁移为准。

## 项目说明

本项目是基于 FastAPI、LangGraph、Next.js、PostgreSQL、Redis 和 APISIX 的 Agent Platform。
业务能力包括 RAG、SQL Agent、智能客服、旅游规划和选品。

## 协作原则

- 面向用户的输出一律用中文，包括过程叙述、进度说明和结论；代码、命令、标识符和必要术语保持原文。
- 先检查工作区与相关代码，再确定最小改动范围；保留其他会话的修改。
- Bug 先定位根因；不顺手扩大任务范围，不改变未要求的业务行为。
- 复用既有路由、Runtime、注册表、契约和审批机制，不建立平行实现。
- 架构、公共契约或关键行为变化时，补充能识别回归的验证。
- 按风险和依赖范围选择最小充分测试；安全、权限、状态和公共契约变化必须覆盖对应边界。
- 详细测试分层统一见 testing-guide.md，使用 T0–T3。
- 不以扩大 mock、删断言、skip 或 xfail 掩盖失败。
- 不执行默认全量回归；测试选择遵循 testing-guide.md。
- 开发与测试优先复用本机依赖及 Docker BuildKit 缓存；依赖输入未变时不重复安装浏览器或重建依赖层。
- 未实际执行的验证不得报告为通过。

## 架构与安全红线

- 沿用 Router / Runtime / Domain Agent / Skill / Tool 的职责边界；MCP 是接入适配方式。
- 已有 registry、manifest、schema、迁移、生成器或发布指针时，以其作为唯一事实源。
- 身份只信任服务端验证后的上下文；资源读写执行资源级授权和租户隔离。
- SQL 使用参数化、只读权限、授权表范围和现有安全校验；不得绕过或降级为宽权限执行。
- 有副作用的 Tool 经过现有 Governance、确认/审批和幂等机制。
- LLM、Prompt 版本、用量和计费使用统一链路，不在调用点重复实现。
- 外部失败须显式返回错误或按既有契约降级；不得吞异常、伪造成功或静默丢任务。
- 关键链路保留必要的结构化日志与 Trace；敏感数据不得写入日志。

## 按需读取

| 任务                                         | 现行文档                                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 网关、部署、异步运行                         | [system-overview.md](docs/architecture/system-overview.md)                                                                       |
| Router、LangGraph、Runtime、SSE、Checkpoint  | [ai-runtime.md](docs/architecture/ai-runtime.md)                                                                                 |
| Provider、凭据和跨域服务关系                 | [domain-service-map.md](docs/architecture/domain-service-map.md)                                                                 |
| 新增或修改 Agent、Skill、Tool、Workflow、MCP | [tool-skill-guide.md](docs/development/tool-skill-guide.md)                                                                      |
| 测试策略                                     | [testing-guide.md](docs/development/testing-guide.md)                                                                            |
| 常用启动、测试和调试命令                     | [commands.md](docs/operations/commands.md)                                                                                       |
| 旅游、客服、RAG、SQL 业务                    | [旅游](docs/domains/travel.md)、[客服](docs/domains/customer-service.md)、[RAG](docs/domains/rag.md)、[SQL](docs/domains/sql.md) |

普通 Bug 优先阅读相关源码与测试。只阅读当前任务需要的文档，不要求加载整个 README 或 docs；历史报告不作为默认上下文。

## 长任务续工

- 简单任务不创建任务文件；跨阶段或跨会话任务使用 docs/tasks/<task-id>/TASK.md 与 PROGRESS.md。
- TASK 记录目标、范围和验收；PROGRESS 记录证据、阻塞和下一步。
- 恢复任务时核对任务文件、Git 状态、代码和测试；无证据不标记完成。
- 临时事项处理后回到原计划；用户改变、暂停或取消目标时遵从最新要求。

## 文档维护与交付

- 架构、接口、数据状态或开发契约变化时，更新受影响的现行文档；普通内部修复无需例行写报告。
- 文档与代码冲突时，核实当前代码、配置、迁移和测试后再修正文档。
- 文档移动或删除前检查 Git 跟踪状态、引用、工具依赖和是否属于在途任务。
- 交付时简述改动、实际验证结果、文档同步情况及未完成风险。



## Git Worktree 并行开发规范

1. 每个独立开发任务使用独立 Git Worktree + `task/<任务标识>` 分支；同一任务跨会话继续时复用原 Worktree。

2. 开始修改前，必须检查当前分支、工作区状态和 `git worktree list`，确认任务所在目录。

3. 如果当前位于主工作树，新任务必须基于最新的已提交 `main` 创建独立 Worktree；不得直接修改主工作树。

4. 如果发现未提交修改、分支冲突或任务归属不明，停止创建和切换，先报告并保护现有改动。

5. 创建 Worktree 后，只能在对应任务目录中开发；如果无法自动切换工作目录，必须告知用户正确路径，不得继续在原目录修改。

6. 各 AI 不得修改其他任务工作树，不得擅自合并到 `main`。

7. 完成任务后提交代码，报告 Commit SHA、改动摘要、测试结果、遗留问题及合并建议。

8. 合并必须经过集成审查及必要测试，冲突不得简单覆盖。合并前不得自动删除工作树。

9. 清理 Worktree 前检查未提交文件、未合并提交及忽略文件；不得丢失重要数据或任务记录。

10. 禁止使用 `reset --hard`、`clean -fd`、强制推送等破坏性命令跳过检查。

11. Worktree 不隔离数据库、Docker、端口和共享文件；涉及共享资源的操作必须独立隔离或串行执行。

原则：**任务自动隔离、开发独立进行、合并统一审核、历史可追溯。**
