# 持续进度

## 当前状态

`IN_PROGRESS`。Planner 契约整改和 checkpoint 策略核验已合并；当前实施客服 `full_path` 评测闭环，Worktree `C:\Users\wh\.codex\worktrees\enterprise-agent-cs-eval-closure\agent`，分支 `codex/cs-fullpath-eval-contract`，基于 `0a647972d3e41a76a586ccf0bd6ec1f204a4aac0`。本轮只允许离线/代码验证，不调用真实模型或 Provider。

## 已完成并合并

- 构建版本身份追溯：PR [#346](https://github.com/Jayus-07/agent/pull/346)，commit `92b92839889fc337d8c20f9a0b86370a5fe6303f`，已合并。此前核验证据包括 13 项定向测试通过、Ruff/py_compile/Compose 配置检查通过；Docker rebuild 与 `/health.build` 未验证。
- Langfuse 依赖/文档清理：PR [#344](https://github.com/Jayus-07/agent/pull/344)，已合并；PR 检查通过。合并后可信 push 的真实 RAG API 评测因离线限制在任务步骤启动前取消，不代表 Live 通过。
- Planner 完整 Plan 契约和有效性：PR [#347](https://github.com/Jayus-07/agent/pull/347)，head `26cb238974e7033fcae80650aa9d58f61197c342`，squash merge `c7c6b2d134a233aefdc194f23eefff87fd96bf87`，已合并。PR 免费 CI 全通过；本地定向集合 **142 passed, 1 既有收集警告**；Ruff、py_compile、diff check 通过。自动 Review 的“运行时补全误记为模型成功”已修复，评分使用补全前模型计划并标记 `runtime_adjusted`。
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

## 客服 full_path 评测闭环（本轮代码已实现，事实裁判待补）

- Runner 继续走生产 `MultiAgentSystem.stream_events`；终态答案优先取 `done.answer`，同时记录稳定来源 ID；每轮重新初始化观察结果，前轮 clarification/action 不会替末轮通过。
- 新增仅评测调用安装的请求级观察器，从客服图终态捕获规范化 `next_action`（answer/refuse/handoff/propose/propose_with_gap）；普通业务请求未安装观察器，不改变 SSE 或业务行为。Route `hit`、`miss_non_cs`、`clarify_weak` 都有反向断言。
- 字面 `must_not_contain` 保持精确文本检查；概念级 `forbidden_facts` 不再拿标签本身做子串匹配。存在语义禁答约束、但没有固定语义裁判时，该样本标为 `skip`，不会冒充 `pass`。
- `allowed_facts` 只在运行时提供显式 `fact_id(s)` 且与 expected 锚点可比时执行白名单断言；普通 `doc_id` 不冒充事实 ID。当前 CS Knowledge 只透传来源/FAQ ID，DB 专家也没有统一事实 ID；manifest 同时说明 `cs_eval_kb` 为 test-only 且 live 消费接线未完成，所以这类样本当前标记 `skip`。
- 影响范围：锁版 v2.2 共 324 条，其中 185 条有 `allowed_facts`、85 条有语义级 `forbidden_facts`、107 条有字面 `must_not_contain`；304 条带 `next_action` 和 `cs_route`。
- 下一步：为测试 KB 和 DB 查询建立稳定的事实 ID/字段来源契约并接入 `cs_q3_acceptance`；再选定经人工校准的语义裁判验证 `forbidden_facts`，在真实模型/Provider 未授权期间只做离线规则与契约测试。
- 本地定向测试 **38 passed**；Ruff、`py_compile`、`git diff --check` 通过，未调用真实模型/Provider。pytest 退出码为 0；结束时另有既有 `TaskService.ensure_schema` 连接池超时日志，需要在后续基础设施检查中确认是否与测试环境连接池配置有关。
- 部署与健康验收为 `UNVERIFIED`：当前没有 `.env`，Docker Desktop Linux Engine 管道不可用，`docker compose ls`/`docker stats` 均无法连接 daemon；未尝试启动或重建共享栈。Docker 可用、环境完整且共享栈归属确认后，再仅重建受影响服务并检查 `/health.build`。

## 未完成全局项

- 客服 full_path 的 route 与 `next_action` 已由生产路径/请求级观察器断言；`allowed_facts` 与概念级 `forbidden_facts` 已改为缺证据 `skip`。稳定事实 ID、测试 KB 到 Live 的绑定、经校准语义裁判仍未完成，不能发布 CS 全路径质量通过率。
- RAG/SQL/旅游/E2E/记忆/工具及多 Agent/办公的 Live 有效性、权限、恢复、并发、SSE 和真实来源需逐模块盘点并补证；不能把离线通过视为模型质量通过。
- Checkpoint 生产策略已核实：普通主图和客服图默认关闭；TaskGraphExecutor 与支持跨轮/中断恢复的旅游图使用 PostgreSQL checkpoint。后续若新增公开暂停/恢复链路，再据其状态契约和权限模型评审。
- 成本、BadCase 回流、可观测和发布门禁需以当前代码/CI/运行证据复核。
- Python/LLM/后端学习清单需要 Demo 运行证据；生产代码覆盖不能代替独立 Demo。
- 当前离线限制下，真实模型质量、Provider 故障演练、生产部署与上线验收均未完成。

## 当前阻塞与解决办法

- 部署验收当前为 `UNVERIFIED`，原因是任务 Worktree 缺 `.env` 且共享 `agent` 项目合并了多工作树 Compose 配置；不要用单工作树配置直接重建共享栈。先确认共享部署上下文并定向重建受影响服务，随后核对正确的 build identity 健康接口。
- 真实模型凭据未授权隔离，本轮只跑 fake/离线边界。解除限制后建立独立评测绑定与 Run ID，再执行 Planner Live，报告实际样本量、模型/Prompt/Commit、成本、延迟和 validity。

## 下一步

1. 客服评测闭环：建立 KB/DB 稳定事实 ID、接通 test-only `cs_eval_kb` 与 full_path suite；之后接入有版本与人工校准的语义裁判，验证 `forbidden_facts` 并发布有效样本结果。
2. 逐模块复核 RAG、SQL、旅游、记忆、工具/MCP、办公与多 Agent 的恢复、权限、并发、SSE 与成本/Trace 证据，按证据选择最小独立修复。
3. 对照 Python/LLM/后端知识清单核查独立 Demo 运行证据；最后在安全部署上下文执行 Docker 重建与健康身份验收。
