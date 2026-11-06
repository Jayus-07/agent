# 持续进度

## 当前状态

`IN_PROGRESS`。当前子任务为 Planner 完整 Plan 契约与降级有效性，位于 Worktree `C:\Users\wh\.codex\worktrees\enterprise-agent-planner-contract\agent`，分支 `codex/planner-plan-validation`，起始 HEAD `74a9b38bbb2c428509021ef6ba841d5008f0cf5d`。本轮只允许离线/代码验证，不调用真实模型或 Provider。

## 已完成并合并

- 构建版本身份追溯：PR [#346](https://github.com/Jayus-07/agent/pull/346)，commit `92b92839889fc337d8c20f9a0b86370a5fe6303f`，已合并。此前核验证据包括 13 项定向测试通过、Ruff/py_compile/Compose 配置检查通过；Docker rebuild 与 `/health.build` 未验证。
- Langfuse 依赖/文档清理：PR [#344](https://github.com/Jayus-07/agent/pull/344)，已合并；PR 检查通过。合并后可信 push 的真实 RAG API 评测因离线限制在任务步骤启动前取消，不代表 Live 通过。

## 当前子任务：Planner 完整 Plan 与降级有效性

### 本轮实施与离线验证

- 新增无静默修复的完整 Planner Plan 结构校验，覆盖根字段、节点字段、授权 capability、步骤唯一性、依赖引用、重复边、环和最大依赖深度。
- Planner Live 评测路径标记执行来源并绕过计划缓存；结构错误按质量失败，Provider/预算/上下文/运行故障按环境错误分类，空模型计划触发的兜底不能记为模型通过。
- 将 Planner 离线 scorer 扩展为精确 capability 多重集、依赖边和步骤范围评分；更新 20 条样本到当前业务域与能力名，并要求完整边集/步骤界限。
- Run Validity、Release Gate、台账对 Planner 单条 Live 错误/跳过 fail-closed；可见当前错误证据可推翻历史缓存 `VALID`。
- 样本校验与 `MODULE_KINDS` 同源，更新评估治理文档、数据集说明和 RAG Smoke 定向清单。
- 补齐跨模块报告有效性解析：当前 Planner Live 错误可推翻旧 `VALID`，已记录的 `INVALID` 保留。
- 处理 PR 自动 Review 的 P2：评测改用过滤/知识步骤补全/KB 注入前的模型计划，并显式记录 `runtime_adjusted`；运行时仍使用策略修正后的计划执行。回归覆盖模型漏出 `rag.search` 时被补齐也必须按原模型计划判分。

### 已有验证证据

- Planner/数据集/Release Gate/RAG Smoke 定向集：**140 passed**；覆盖无缓存 Live 入口、非法/越权原始输出、过期未授权缓存、代理降级响应、Provider/预算/超时分类、完整边集、步骤范围、重复 capability 与历史有效性缓存。此总数包含有效性台账回归。
- Review 修复后重新运行同一完整定向集：**142 passed, 1 warning**；警告为既有 `Eval TestCase` pytest 收集提示。
- `py_compile`、Ruff（`--ignore I001,F401,E402`）、`git diff --check` 通过；Planner v2.0 `case_count=20` 与 canonical 内容 hash 在测试及 manifest 生成器核验中一致。
- PR #347 原提交的免费 CI 全绿：secret scan、Evaluation V2 admin/free contract、RAG change detection、RAG Smoke preflight 与离线 smoke 汇总通过；真实 API Job 为 `SKIPPED` 且 `steps=[]`。P2 修复推送后需以新 commit 的 CI 结果为准。
- 未调用真实模型/Provider；测试使用 fake 响应。没有运行 Planner Live 评测，因此没有新的模型质量分数。

### 本轮待办

- 已完成 Planner 契约、Runtime 评测入口、Live Runner、数据集、Run Validity 和定向 Smoke 清单修改；Review 修复后的 142 项定向测试已通过，见上方证据。
- Docker/资源复核：Windows 可用内存 **3.68 GiB**；共享 Compose 项目 `agent` 有 **20 个运行容器**，其配置来自 **4 个工作树**。当前任务 Worktree 没有 `.env`，`docker compose config` 因缺少必需数据库变量失败；`devctl.bat rebuild` 会将此 Worktree 的 Compose 配置固定到共享项目 `agent` 并重建多组服务，存在覆盖其他工作树运行配置的风险。因此未重建/重启共享栈，当前代码部署及 `/health.build` 为 **`UNVERIFIED`**。解除方式：在确认共享栈归属/配置且安全地补齐任务 Worktree 环境后，复用共享栈只重建受影响服务并检查 `/health.build`，或安排独立维护窗口执行。
- 当前 PR #347 已有原提交 `ae16bce90a98c880df73e230e1077df217348d11`；自动 Review 提出的 P2 已修复并通过本地新增回归，待更新分支、等待新 commit CI、按用户授权合并并核对 main。真实 Planner Live 仍待模型凭据/授权验证。

## 未完成全局项

- 客服 full_path 尚有 `next_action`、事实 ID、语义禁答和未命中路由等待闭环断言。
- RAG/SQL/旅游/E2E/记忆/工具及多 Agent/办公的 Live 有效性、权限、恢复、并发、SSE 和真实来源需逐模块盘点并补证；不能把离线通过视为模型质量通过。
- Checkpoint 生产启用应按具体链路的跨请求恢复契约决定，需重新核对 checkpoint-policy 分支/PR 当前状态；普通会话图不因生产环境默认打开。
- 成本、BadCase 回流、可观测和发布门禁需以当前代码/CI/运行证据复核。
- Python/LLM/后端学习清单需要 Demo 运行证据；生产代码覆盖不能代替独立 Demo。
- 当前离线限制下，真实模型质量、Provider 故障演练、生产部署与上线验收均未完成。

## 当前阻塞与解决办法

- 部署验收当前为 `UNVERIFIED`，原因是当前 Worktree 缺 `.env` 且 `agent` 项目合并了多工作树 Compose 配置；不要用单工作树配置直接重建共享栈。先确认共享部署上下文并定向重建受影响服务，随后核对 `/health.build` commit/build identity。
- 真实模型凭据未授权隔离，本轮只跑 fake/离线边界。解除限制后建立独立评测绑定与 Run ID，再执行 Planner Live，报告实际样本量、模型/Prompt/Commit、成本、延迟和 validity。
