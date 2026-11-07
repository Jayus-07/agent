# 持续进度

## 当前状态

`IN_PROGRESS`。Planner 契约整改（PR #347）、checkpoint 策略（PR #345）、客服 `full_path` test KB/事实 ID 接线（PR #350）和模块化数据契约（PR #352）均已合并。当前阶段继续完成离线验收；真实模型/Provider、fixture 导入和生产部署需等离线测试通过后、具备隔离凭据与预算再做。

2026-11-07 更新：客服 full_path 的事实来源与 test KB 接线通过 PR [#350](https://github.com/Jayus-07/agent/pull/350) 合并，head `6e776e057668f5b6e00768f13cbe35003d0e47ff`，merge SHA `6240f556096a92969c3b86f657902bf4ebaeaa93`。最新定向测试 140 passed；PR head 的免费离线检查通过，真实 API Job 跳过，三项自动 Review P2 已修复并解决。合并后的 main RAG real-API Job 在零执行步骤时按离线限制取消；required summary 因 `SMOKE_RESULT=cancelled`、缺少可信 8-case Live 结果而失败。该发布门禁当前 `BLOCKED`，不是离线代码测试失败；未执行模型调用。fixture 导入/真实检索质量与部署健康仍 `UNVERIFIED`。

当前子任务验收证据与进度见 [cs-fact-id-contract](../cs-fact-id-contract/TASK.md) 和其 `PROGRESS.md`。

2026-11-07 更新：数据契约与 E0 preflight 通过 PR [#352](https://github.com/Jayus-07/agent/pull/352) 合并，head `3773a36a4d5d22f96175c6c0a9e86a43ec423166`，merge SHA `d34ed983643e7c1bbee10b4d03a24ed921cd58c5`。本地定向验证 70 passed；PR 免费离线检查通过，自动 Review 的 P1/P2 已修复。合并后 RAG Live 曾由 main push 自动排入；在零执行步骤时取消，未发生 Provider 调用，summary 因缺少 Live 结果失败。这暴露出自动触发与“离线先行”冲突，本任务将改为显式手动 opt-in。

2026-11-07 更新：RAG 付费评测显式 opt-in 通过 PR [#353](https://github.com/Jayus-07/agent/pull/353) 合并，head `709b1401847b1410b921da97d91d862973a21f4b`，merge SHA `8b2ba9aafa3d5667255892daa5923ada92cc7d98`。本地 15 项定向测试通过，PR 免费 preflight、required gate、secret scan 均通过，自动 Review 无发现；PR Live Job 为 `skipped`。合并后的 main push run `42335026667` 中 Live 与免费 preflight 均跳过、required gate 成功报告 `DEFERRED`，无 Provider 调用。

当前离线审计修复了评测历史列表的有效性一致性：PG 摘要有效性为空或旧 `VALID` 时，会在工作线程读报告复核；PG 不可用走文件降级时也复用统一裁决器，旧报告会补算，当前错误证据可推翻旧 `VALID`。全量错误时保留已识别的数据库/Provider/预算原因。定向集合 **61 passed**，Ruff、`py_compile`、YAML 解析和 `git diff --check` 通过。pytest 收尾阶段 TaskService 尝试连接本机无密码 PostgreSQL 并超时，测试进程退出码 0、断言全部通过。由于本次改动后端 API，本机 rebuild/health 为 `UNVERIFIED`：Docker Engine named pipe 不存在、该 Worktree 无 `.env`；未触碰共享栈。恢复 Docker、补齐隔离环境并确认栈归属后再定向重建。

后续核对发现详情 API 也会向管理端质量对比暴露报告中的历史 `run_validity`。当前分支已补齐详情响应重算，且只改响应副本；新增覆盖旧 `VALID` 被错误证据推翻、持久化 `INVALID` 不降级及不改写原报告。列表/详情/Release Gate/API workflow 定向集合 **63 passed in 41.23s**，Ruff、`py_compile`、`git diff --check` 通过。pytest 退出清理仍报告本机 PostgreSQL 未提供密码，最终退出码 0。Docker Engine named pipe 不可用，无法按仓库规则重建后端服务或检查健康身份，部署验收 `UNVERIFIED`；未运行任何 Provider。

PR [#355](https://github.com/Jayus-07/agent/pull/355) 已于 2026-11-07 合并，head `5ff0a81bfa653e2a0bd777fb77e25569068f9807`，merge SHA `489d2131e44ced6f07e0cfb97524fff24f6c5a6f`；PR 离线 preflight、RAG 汇总门、secret scan 与自动 Review 均通过，Review 无发现，Live job 跳过。合并后 main 的 RAG 汇总门与 secret scan 通过，Live/preflight 按 push 策略跳过。

随后用固定报告复现 RunGuard 停止状态缺少有效性分类：99/100 样本通过、1 条取消跳过时，旧逻辑返回 `VALID` 和 Release Gate `PASS`。当前分支已为取消、deadline、预算与 token 熔断补上 `INVALID_*` 分类和 fail-closed 门禁回归；列表/详情、RunGuard 分类、Release Gate、取消生命周期、台账和 RAG workflow 定向集合 **82 passed in 39.90s**。Ruff、`py_compile`、`git diff --check` 通过，pytest 退出码 0；收尾时仍有本机 PostgreSQL 未提供密码的连接池超时日志，不影响断言。Docker Engine named pipe 不可用、当前 Worktree 无 `.env`，后端 rebuild/health `UNVERIFIED`，未触碰共享栈或 Provider。

## 已完成并合并

- 构建版本身份追溯：PR [#346](https://github.com/Jayus-07/agent/pull/346)，commit `92b92839889fc337d8c20f9a0b86370a5fe6303f`，已合并。此前核验证据包括 13 项定向测试通过、Ruff/py_compile/Compose 配置检查通过；Docker rebuild 与 `/health.build` 未验证。
- Langfuse 依赖/文档清理：PR [#344](https://github.com/Jayus-07/agent/pull/344)，已合并；PR 检查通过。合并后可信 push 的真实 RAG API 评测因离线限制在任务步骤启动前取消，不代表 Live 通过。
- Planner 完整 Plan 契约和有效性：PR [#347](https://github.com/Jayus-07/agent/pull/347)，head `26cb238974e7033fcae80650aa9d58f61197c342`，squash merge `c7c6b2d134a233aefdc194f23eefff87fd96bf87`，已合并。PR 免费 CI 全通过；本地定向集合 **142 passed, 1 既有收集警告**；Ruff、py_compile、diff check 通过。自动 Review 的“运行时补全误记为模型成功”已修复，评分使用补全前模型计划并标记 `runtime_adjusted`。
- 客服 full_path test KB 与事实 ID：PR [#350](https://github.com/Jayus-07/agent/pull/350)，head `6e776e057668f5b6e00768f13cbe35003d0e47ff`，squash merge `6240f556096a92969c3b86f657902bf4ebaeaa93`，已合并。最终定向测试 **140 passed in 50.56s**；新 head 的免费 PR 检查通过，真实 API Job 按离线限制跳过；三项 Review P2 已解决。合并后 main 的 RAG Live Job 在零执行步骤时取消，汇总 Job 因无 Live 结果失败；不代表离线契约失败或 Live 质量通过。Docker rebuild/health、fixture 导入和检索质量仍 `UNVERIFIED`。
- 模块化数据契约和 E0 preflight：PR [#352](https://github.com/Jayus-07/agent/pull/352)，head `3773a36a4d5d22f96175c6c0a9e86a43ec423166`，squash merge `d34ed983643e7c1bbee10b4d03a24ed921cd58c5`，已合并。本地定向测试 **70 passed**，PR 免费 CI 通过；模块 schema 校验、SQL gold 字段、Curator fail-closed 和 RAG preflight 均覆盖。合并后 main 自动排入的真实评测在零步骤时取消，未发生 Provider 调用。
- RAG Live 显式 opt-in：PR [#353](https://github.com/Jayus-07/agent/pull/353)，head `709b1401847b1410b921da97d91d862973a21f4b`，squash merge `8b2ba9aafa3d5667255892daa5923ada92cc7d98`，已合并。PR 离线 preflight / required gate / secret scan 通过；Live job `skipped`。合并后 main push 验证为 gate `success` + `DEFERRED`，没有 Provider 调用；真实评测只允许 `main` 上显式 `run_live_eval=true`。
- Checkpoint 启用策略：PR [#345](https://github.com/Jayus-07/agent/pull/345) 文档修改已合并；本轮按当前配置、源码和测试复核，无需开启主图普通对话或客服图的生产开关，详细依据见下方。

## 已完成子任务：Planner 完整 Plan 与降级有效性

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
- PR #347 最终 head 的免费 CI 全绿：secret scan、Evaluation V2 admin/free contract、RAG change detection、RAG Smoke preflight 与离线 smoke 汇总通过；PR 真实 API Job 为 `SKIPPED` 且 `steps=[]`。
- 合并后 main 的 secret scan 与 Evaluation V2 Contract 通过。可信 push 的 RAG 真实评测 Job 在 `queued` 后取消，Job `steps=[]`；RAG 汇总门按设计因未取得真实评测成功而失败。此结果遵守离线限制，不代表 Live 通过。
- 未调用真实模型/Provider；测试使用 fake 响应。没有运行 Planner Live 评测，因此没有新的模型质量分数。

### Planner 子任务验收边界

- Docker/资源复核：Windows 可用内存 **3.68 GiB**；共享 Compose 项目 `agent` 有 **20 个运行容器**，其配置来自 **4 个工作树**。当前任务 Worktree 没有 `.env`，`docker compose config` 因缺少必需数据库变量失败；`devctl.bat rebuild` 会将此 Worktree 的 Compose 配置固定到共享项目 `agent` 并重建多组服务，存在覆盖其他工作树运行配置的风险。因此未重建/重启共享栈，当前代码部署及 `/health.build` 为 **`UNVERIFIED`**。解除方式：在确认共享栈归属/配置且安全地补齐任务 Worktree 环境后，复用共享栈只重建受影响服务并检查 `/health.build`，或安排独立维护窗口执行。
- 真实 Planner Live 仍待模型凭据/授权验证；当前只有 fake/离线验证，没有新的模型质量分数。

## Checkpoint 策略核查结论

- 主图普通 `/agent`：`MAIN_GRAPH_CHECKPOINTER_ENABLED` 代码默认 `false`；每次普通图执行使用新的不透明 `agent-<UUID>` thread_id，没有按该 ID 恢复同一次中断执行的公开入口。多轮历史由 MemoryService 管理。保持生产关闭是正确选择。
- 客服图：`CS_CHECKPOINTER_ENABLED` 默认 `false`；图入口每轮从客服业务存储重新加载状态并清空单轮执行字段，`supports_interrupt=false`，没有客服图 `Command(resume=...)` 入口。客服对话/确认状态走业务存储，不需要仅因生产而打开图级 checkpoint。
- 后台 TaskGraphExecutor：不受主图开关控制，始终构造 PostgreSQL Checkpointer；暂停、等待用户、Worker 接管和重试恢复依赖它。此类提供恢复契约的任务必须保持持久化，不能以 MemorySaver 代替。
- 旅游图：主线允许复用稳定会话 thread_id，存在 interrupt 和 `Command(resume=...)` 恢复路径；`docker-compose.yml` 默认 `TRAVEL_CHECKPOINTER_ENABLED=true`，运行中的 `agent-app-1` 也显式设置为 `true`。其多轮改单/人工决策恢复需要持久 checkpoint；只读问答明确不挂载。
- 当前容器没有设置主图或客服 checkpoint 环境变量，代码默认关闭；部署容器显式设置 `TRAVEL_CHECKPOINTER_ENABLED=true`。该环境核对只证明变量配置，不代替目标 Worktree 部署/健康验收。
- 本轮未修改运行时代码。上述策略已由 PR #345 的现行 [AI Runtime 文档](../../architecture/ai-runtime.md)记录，本轮按 builder、runner、customer-service state loader、task executor、travel resume 调用及现有 checkpoint 测试复核。

## 客服 full_path 评测闭环（事实来源接线已实施，Live 与语义裁判待补）

- Runner 继续走生产 `MultiAgentSystem.stream_events`；终态答案优先取 `done.answer`，同时记录稳定来源 ID；每轮重新初始化观察结果，前轮 clarification/action 不会替末轮通过。
- 新增仅评测调用安装的请求级观察器，从客服图终态捕获规范化 `next_action`（answer/refuse/handoff/propose/propose_with_gap）；普通业务请求未安装观察器，不改变 SSE 或业务行为。Route `hit`、`miss_non_cs`、`clarify_weak` 都有反向断言。
- 字面 `must_not_contain` 保持精确文本检查；概念级 `forbidden_facts` 不再拿标签本身做子串匹配。存在语义禁答约束、但没有固定语义裁判时，该样本标为 `skip`，不会冒充 `pass`。
- PR #350 已将稳定事实来源接入 main：登记 suite 的 full_path 通过服务器内部 scope 只检索 `audience=test` 的 `cs_eval_kb`；固定 fixture manifest 校验文档 hash、RAG version 与精确章节；KB 事实 ID 来自回答实际引用的 chunk/章节 evidence，不用按文件去重的 SourceCard，也不受五条展示截断影响；订单事实只从 `sandbox_order_service` 返回的 DEMO 订单结构化字段生成。普通客服仍保持 customer 授权和生产 FAQ 行为。离线验证通过；fixture 实际索引/检索、Live 质量与部署健康仍未验证。
- Fixture 导入器要求显式 `--apply`，并将清理和写入限制在专用 test KB。本轮遵守离线限制，没有运行导入器或 embedding Provider；实际索引状态、检索质量和真实模型质量仍未验证。
- `allowed_facts` 有了运行时事实 ID 后可执行白名单断言；概念级 `forbidden_facts` 仍没有经人工校准的语义裁判，缺少判据的样本继续标记 `skip`，不会冒充 `pass`。
- 影响范围：锁版 v2.2 共 324 条，其中 185 条有 `allowed_facts`、85 条有语义级 `forbidden_facts`、107 条有字面 `must_not_contain`；304 条带 `next_action` 和 `cs_route`。
- 当前子任务的离线测试、PR 免费 CI 和合并验收完成后，再独立设计并校准 `forbidden_facts` 语义裁判；真实 fixture 导入与模型评测需等到获准使用隔离 Provider 凭据后执行。
- 上一轮观察器/Runner 定向测试 **38 passed**；PR #350 初版事实 ID 与 test KB 接线验证为 **99 passed in 49.19s**。后续处理自动 Review P2 后，当前修复版最终定向集合为 **140 passed in 50.56s**（退出码 0）。Ruff、`py_compile`、manifest JSON、YAML 与 `git diff --check` 通过，未调用真实模型/Provider。测试收尾另有 `TaskService.ensure_schema` 因本机 PostgreSQL 未提供密码的连接池超时日志，不影响通过结果。
- 部署与健康验收为 `UNVERIFIED`：当前没有 `.env`，Docker Desktop Linux Engine 管道不可用，`docker compose ls`/`docker stats` 均无法连接 daemon；未尝试启动或重建共享栈。Docker 可用、环境完整且共享栈归属确认后，再仅重建受影响服务并检查 `/health.build`。

### 自动 Review P2：补槽后的终态动作识别（PR #349 follow-up）

- 根因：`pending_turn_slot_fill` 由 Supervisor 以 `finish` 收尾；原观察器将所有 `finish` 视作 `answer`，导致补槽后新建待确认提案被误判，且只看顶层状态会读到补槽前遗留的 `need_info`。
- 修复：只在补槽轮检查本轮 `last_expert_result.data.pending_action`。新待确认提案映射为 `propose`，仍缺信息映射为 `propose_with_gap`；本轮未产生新 pending 时按回答/拒绝处理，不使用旧顶层快照。客服业务执行路径未改。
- 回归：观察器和生产 full_path runner 分别覆盖三种终态，包括“旧状态仍 need_info、但当前补槽未生成提案”的失败边界。
- 本地验证：定向评测/观察器/图契约测试 **44 passed**；Ruff、`py_compile`、`git diff --check` 通过。pytest 退出码为 0，但结束日志显示 `TaskService.ensure_schema` 因本机 PostgreSQL `127.0.0.1:5432` 未提供密码而连接超时；该环境连接问题未解决，也未掩盖断言失败。
- PR #349 的免费检查在修复前 head 全通过；真实 RAG API 评测按离线要求跳过。修复后的新 head 仍待 PR CI 与 review 状态复核。
- Docker 重建/健康验收 `UNVERIFIED`：宿主可用内存 6.29 GiB，但 Docker Desktop Linux Engine named pipe 不存在，且当前 Worktree 无 `.env`；未启动或重建共享栈。恢复 Docker daemon、补齐安全的任务环境并确认共享栈归属后，按部署规则重建受影响服务，再检查 `/health.build`。

## 未完成全局项

- 客服 full_path 的 route、`next_action`、稳定事实来源和 test KB 代码接线已在 main；fixture 实际索引/检索与 Live 质量未验证。概念级 `forbidden_facts` 仍因缺少校准裁判而 skip，当前不能发布 CS 全路径质量通过率。
- **离线评测剩余项**：逐模块完成 RAG、SQL、旅游、E2E、客服、Planner/记忆、工具/MCP、多 Agent/办公的数据有效性、Runner 状态分类、错误/跳过有效性、Release Gate 与免费 CI 清单；已经补齐的数据契约需继续确认各模块报告不会把 `INVALID`、`BLOCKED` 或 `SKIPPED` 当成通过。
- **离线可靠性剩余项**：按真实调用链补证超时/重试/降级、幂等、任务断点恢复、SSE 断开、资源/租户授权、并发与调度、人工确认，以及 Trace/模型/Prompt/数据集/Commit/Token/成本归因和 BadCase 回流。
- **离线客服评测剩余项**：`forbidden_facts` 概念级语义裁判尚未校准；制定可复现的离线裁判方案前，相关样本继续标为 `skip/INVALID`，不发布客服全路径质量通过率。
- **独立 Demo 剩余项**：Python 异步/装饰器/数据结构/异常/FastAPI，大模型结构化输出与工具调用，及 Redis/MySQL/Docker/API/并发等学习清单缺少独立运行证据；生产代码覆盖不等于 Demo 验收。
- **部署验收剩余项**：Docker 重建与健康身份仍 `UNVERIFIED`；当前 Docker Engine 不可用且共享 Compose 含多工作树配置，恢复 Docker 并确认共享栈归属后才可安全定向重建。
- Checkpoint 生产策略已核实：普通主图和客服图默认关闭；TaskGraphExecutor 与支持跨轮/中断恢复的旅游图使用 PostgreSQL checkpoint。后续若新增公开暂停/恢复链路，再据其状态契约和权限模型评审。
- 当前离线限制下，真实模型质量、Provider 故障演练、生产部署与上线验收均未完成。

## 当前阻塞与解决办法

- 部署验收当前为 `UNVERIFIED`，原因是任务 Worktree 缺 `.env` 且共享 `agent` 项目合并了多工作树 Compose 配置；不要用单工作树配置直接重建共享栈。先确认共享部署上下文并定向重建受影响服务，随后核对正确的 build identity 健康接口。
- 真实模型凭据尚未确认隔离，当前只跑 fake/离线边界。离线清单通过后，先准备独立评测绑定、Run ID、预算和凭据隔离，再运行 Live 并报告样本量、模型/Prompt/Commit、成本、延迟和 validity。

## 下一步

1. 继续核对各模块 API、台账、报告文件与 Release Gate 的状态一致性，补齐尚未覆盖的错误/跳过/恢复分支。
2. 逐模块完成离线数据有效性、错误/跳过状态、权限/恢复/并发/SSE/成本归因测试；同步完成客服离线语义裁判方案与学习清单 Demo。未通过项继续修复并分别提交 PR。
3. 离线测试与免费 CI 全部通过后，再确认凭据隔离、预算、Run ID 和授权，执行付费 Live；Docker 部署/健康验收在确认安全部署上下文后单独完成。任何未验证项保留 `UNVERIFIED/BLOCKED`，不得报成通过。
