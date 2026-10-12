# 进展记录

更新时间：2026-10-12（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`ed28db13e9b18b88c02b7481ca6b5525ac25e0ec`（PR #40 B1-A 已按用户批准合并）。
- 安全治理工作树：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- 除已合并的 #40 外，其余待集成分支仍以 `a67449b` 为基点；main 当前包含文档提交 `5e48246` 与 B1-A 合并提交 `ed28db1`。PR #40 以 GitHub 正常 merge 流程合并，未改写 main 历史；后续每项合并前仍须重新 fetch 并重验。

## 统一看板

| 项目 | PR | 状态 | CI | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | 已合并至 main，merge commit `ed28db1` | PR head `9cca719` / RAG PR Smoke run 111 SUCCESS，评测 job 按路径 SKIP；合并后隔离 Worktree 定向集 11 passed，merge SHA 无 main workflow/check run | 0 条未解决 review thread；无 GPT review submission | 排队上限/等待超时继续挂账，不扩本 PR |
| B1-B | #42 | 已同步 #40/main，head `d0223b9`，`mergeable=true` | 组合定向集 65 passed、9 xfailed；head RAG PR Smoke run 134 全部 SUCCESS | 3 条旧 review thread 已解决；GPT 修复针对旧 head，未发现新 head 审查 | 等用户批准合并 |
| B1-F | #43 | 已同步 #40/main，head `e619ff1`，`mergeable=true` | 组合定向集 39 passed；head RAG PR Smoke run 135 全部 SUCCESS | 2 条旧 review thread 已解决；GPT 修复针对旧 head，未发现新 head 审查 | 按顺序等 #42 合并及用户批准 |
| RAG R0/R1 | #41 | head `fc27dec` 最终核对完成，未合并 | head `fc27dec` / Secret Scan run 8 SUCCESS；RAG Smoke run 112 SUCCESS | 3 条旧 review thread 均已解决；无未解决 thread | 分支基于 `a67449b`；安全 PR #44 尚未合并；凭据轮换/旧值失效验证未完成，P0 仍阻断放行 |
| 凭据安全治理 | #44 | 安全代码 head `9e92dd6`；后续仅更新任务板，`mergeable=true` | 安全代码 run 131 SUCCESS；任务板提交 `bf30cff` 的 run 136 SUCCESS；head `5bf88b8` 的 tree/diff/history 扫描均 0 命中 | GPT 指出的根 `.env` 加载问题已修复并 resolve；无未解决 thread | Redis/API Key 轮换和旧凭据失效验证需凭据持有人授权；未完成前 P0 BLOCKED |
| RAG MultiQuery P0 | 新 PR | 未启动（依赖门禁未满足） | — | — | 等 PR #41/#42 合并及用户批准 |
| B1-C | #45 | 已同步 #40/main 并解冲突，head `b2e7bbc`，当前 `mergeable=true` | 相关定向集（含 B1-A）140 passed；head RAG PR Smoke run 132 SUCCESS，RAG Smoke、Evaluation、Detect 均 SUCCESS | 父 Trace 所有权、物理完成未知时重试、checkpoint deadline 归一化已修复并 resolve；审查基于旧 head，尚无新 head 复审 | 当前唯一注册表只有 4 个 Workflow；未臆造第 5 个，需确认此范围条件；合并仍待用户批准 |
| B1-D | #47 | 两条新 P1 已修复并推送，head `6acdc54`，mergeable | head RAG PR Smoke run 129 SUCCESS；RAG Smoke、评测与 Detect checks SUCCESS | 并发快照覆盖、归档版本回滚两条 P1 已修复并 resolve；最近 GPT review 针对 `35fbee9`，未复审最新 head | PostgreSQL CAS 集成用例未运行，避免操作共享数据库；合并需用户批准 |
| B1-E | 新 PR | 只读核对；尚未施工 | — | — | 生产实例的 Checkpointer 实际运行状态无可读证据；按门禁暂停实现 |

## 已取得证据

- PR #40 head `9cca719`：按用户批准通过 GitHub 正常 merge 流程合入 main，merge commit `ed28db1`（父提交为 `5e48246` 与 PR head `9cca719`），未改写历史。合并前 run 111 的 RAG Smoke SUCCESS、评测 job 按路径 SKIP、Detect SUCCESS；0 条未解决 inline review thread。合并后在独立干净 Worktree、隔离本地 PostgreSQL 集群运行 `backend/tests/orchestration/workflow/test_selection_decision_event_loop.py` 与 `test_selection_decision_smoke.py`，`11 passed in 24.66s`；merge SHA 没有触发 main workflow/check run。主工作树原有用户修改未动。排队上限/等待超时不在本 PR 中扩展。
- PR #42 原 head `fb57241` 曾为 `58 passed, 2 skipped, 9 xfailed`；同步 #40/main 后 head `d0223b9` 的组合集为 `65 passed, 9 xfailed`，run 134 的 RAG Smoke、Evaluation、Detect 全部 SUCCESS。
- PR #43 原 head `2172208` 曾为 `28 passed`；同步 #40/main 后 head `e619ff1` 的组合集为 `39 passed`，run 135 的 RAG Smoke、Evaluation、Detect 全部 SUCCESS。
- PR #41 head `fc27dec`（基于当时最新 `a67449b`）：RAG 定向回归 `81 passed in 14.05s`；PR 的 `secret-scan` run 8 SUCCESS，RAG Smoke run 112 SUCCESS。前轮真实 Provider/性能/准确率证据未重跑、原样保留。
- PR #41 当前树仍报告根 `.env.example` 与 3 个 Travel 脚本的 4 项 `assigned_secret`；安全 PR #44 中已修正这些代码项，因此 PR #41 需等 #44 合并后再同步并重扫。PR #41 的 `origin/main..HEAD` 可达提交区间扫描为 0；历史讨论记录此前曾清理 PR 分支历史。本任务未重写 main 或其他共享历史。
- 安全 PR #44 head `5bf88b8` 使用 PR #41 的 `scripts/scan_secrets.py` 执行 `--tree`、`--diff origin/main..HEAD`、`--history --range origin/main..HEAD`，三项均 exit 0、0 命中。三个 Travel 脚本从项目根 `.env` 读取 `API_KEY`，进程环境变量优先；用不含真实凭据的临时 `.env` 做隔离行为验证：3/3 脚本均能读取配置，缺少配置时均在网络请求前失败关闭。`py_compile`、`git diff --check` 与 Ruff 导入排序检查通过。PR #44 run 117 SUCCESS；GPT P2 已修复并 resolve。没有真实 Provider 请求。
- PR #42 已同步当前 main（含 #40）至 head `d0223b9`：Eval、LLM governance、Travel intent 与 B1-A 回归集 `65 passed, 9 xfailed in 71.58s`；使用隔离本地 PostgreSQL 和 mock Provider，未连接共享数据库或真实 Provider；GitHub run 134 的三项检查全部 SUCCESS。原 3 条 GPT review thread 已解决。
- PR #43 已同步当前 main（含 #40）至 head `e619ff1`：终态取消、错误协议与 B1-A 回归集 `39 passed in 25.89s`；GitHub run 135 的三项检查全部 SUCCESS。原 2 条 GPT review thread 已解决。
- B1-C PR #45 已同步当前 main（含 #40）至 head `b2e7bbc`，保留 `deadline_sec=900` 并吸收 B1-A 异步 LLM 调用；Workflow / DirectExecutor / RequestContext / SafeToolExecutor 及 B1-A 定向集 `140 passed in 54.39s`，本地 PG 隔离、无真实 Provider。GitHub run 132 的 RAG Smoke、Evaluation、Detect 均 SUCCESS。三条旧 review thread 已 resolve。
- B1-D PR #47 最新 head `6acdc54`：Prompt Service、Release Service、Hot Reload、Prompts API、Release API、Prompt Trace 定向集 `134 passed`；`py_compile`、Ruff、`git diff --check` 均通过。新增提交乱序并发测试：模拟较早 DB commit 已生效但 coroutine 晚恢复，再执行第二次发布，最终快照跟随第二次提交。发布状态测试使用 fake session/repository 与 Fake Redis；未连接共享 PostgreSQL、真实 Redis 或 Provider。PG CAS 真库用例因共享 DB 禁令未运行。head RAG PR Smoke run 129 SUCCESS；旧提交 `35fbee9` 的 run 123 与中间 head 的 run 127 均不作为此 head 验收。两条 P1 review thread 已 resolve；GPT 审查基于 `35fbee9`，最新 head 尚未复审。
- B1-E 只读核对：仓库 Docker Compose 的 Travel Checkpointer 默认值为 PostgreSQL 开启，应用配置的默认值则为关闭；运行时图状态区分 `healthy/degraded/disabled`，生产启动对缺少 PostgresSaver 依赖有显式校验。现有 interrupt 测试使用 `MemorySaver`，不能证明生产配置。当前工作区没有生产部署配置读取或实例运行状态访问，故不能确认实际生产后端与 interrupt 开关；未修改 B1-E 代码，需取得不含凭据的生产配置/运行状态证据后再施工。
- 风险复核另发现 Travel 脚本保留预置测试登录常量；未被 Secret Scan 识别为可验证凭据，当前用途似为测试账号，但是否只在隔离环境有效尚未确认。看板和 PR 未记录其值；需代码所有者确认非生产复用后才能消除此项风险。
- 安全分支另将根 `.env.example` 的 JWT 项改为明显占位符，避免把可复制的静态签名值当成配置；安全代码 head `9e92dd6` 的 run 131 SUCCESS，后续任务板提交 `bf30cff` 的 run 136 SUCCESS。

## 凭据轮换与失效验证（BLOCKED）

本任务未访问真实 Redis/API 服务，未撤销、创建、修改或轮换真实凭据。轮换需要凭据持有人授权并在实际 Secret Manager/服务端操作：

1. **Redis**：授权运维创建/轮换 Redis ACL 凭据；把新值写入 Secret Manager 并更新所有消费者部署；使用新凭据验证连接和应用读写；撤销旧凭据；用受控验证确认旧凭据收到认证拒绝。
2. **内部 API Key**：授权服务负责人在 API Gateway/密钥管理端签发新 Key；更新运行脚本的 `API_KEY` 环境配置；确认新 Key 的受控请求成功；撤销旧 Key；确认旧 Key 请求返回认证/授权拒绝。
3. 验证日志、命令行历史、测试和 PR 正文只记录凭据 ID、时间与成功/拒绝状态，不记录密钥值。

在旧 Redis 密码和 API Key 均已撤销并验证失效前，不能宣称 P0 安全风险关闭。

## 历史清理风险与操作方案

- 当前 PR #41 可达历史扫描为 0；GitHub 既有审查记录显示此前清理的是 PR 分支历史。此次接管没有修改 main 或其他共享分支历史。
- 不再单方面重写公开共享历史。若凭据持有人确认仍需历史清理：先列出受影响 refs、fork、镜像和长期克隆；确认旧凭据已失效；约定维护窗口并通知消费者；保留不含凭据值的 commit/ref 证据；再由仓库管理员评估对受影响 refs 做协调式清理，并要求参与者重新克隆/同步；清理后复扫当前树和可达历史。

## 下一步

1. PR #42/#43 当前 head 的测试与 CI 均通过；已向用户请求仅合并 #42 的批准，等待回复。获批后只合并 #42，检查 main，再准备 #43 的独立批准请求。#45 的 run 132 已通过，但按顺序不提前合并。
2. 凭据持有人授权后完成 Redis 与 API Key 轮换、部署更新、撤销旧值及旧值认证失败验证；确认测试登录常量仅供隔离环境使用。此前 P0 BLOCKED。
3. PR #44 获批合并后，将 PR #41 同步最新 main 并复扫；只有 P0 风险处理满足放行要求且用户批准，才继续合并 RAG/Runtime PR。
4. 只有 PR #41 和 #42 合并后才施工 RAG MultiQuery P0；B1-C/#45、B1-D/#47 等用户审批。B1-E 先取得生产 Checkpointer 实际状态证据，再独立施工。
