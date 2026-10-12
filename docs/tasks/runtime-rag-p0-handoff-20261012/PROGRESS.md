# 进展记录

更新时间：2026-10-12（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`b0f71bc93727279780c68ea6334c9fe6f4731abf`（PR #40/#42/#43 已按用户批准依次合并）。
- 安全治理工作树：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- #41/#45/#47 仍需基于各自依赖与顺序同步最新 main；#44 已同步至 `b0f71bc`。合并均使用 GitHub 正常 PR 流程，不改写 main 历史；每项合并前重新 fetch 并重验。

## 统一看板

| 项目 | PR | 状态 | CI | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | 已合并至 main，merge commit `ed28db1` | PR head `9cca719` / RAG PR Smoke run 111 SUCCESS，评测 job 按路径 SKIP；合并后隔离 Worktree 定向集 11 passed，merge SHA 无 main workflow/check run | 0 条未解决 review thread；无 GPT review submission | 排队上限/等待超时继续挂账，不扩本 PR |
| B1-B | #42 | 已按用户批准合并至 main，merge commit `0c471fb` | 合并前 head `d0223b9` 的 RAG PR Smoke、Evaluation、Detect 全部 SUCCESS；合并后隔离 worktree 定向集 65 passed、9 xfailed；merge SHA 无 main workflow/check run | 3 条旧 review thread 已解决 | 已完成；Usage Attribution 与预算账本按 `tenant=eval` 对齐 |
| B1-F | #43 | 已按用户批准合并至 main，merge commit `b0f71bc` | 合并前 run 138 的 Detect、RAG Smoke Evaluation、RAG Smoke 全部 SUCCESS；合并后隔离 worktree 定向集 39 passed；merge SHA 无 main workflow/check run | 2 条旧 review thread 已解决 | 已完成；逻辑取消与物理 Provider 停止边界保持明确 |
| RAG R0/R1 | #41 | head `b543fb0`，未合并；已补强 Secret Scan 短密码规则 | 新增扫描器定向集 `10 passed in 10.90s`；本次 head workflow run 尚未出现；既有 RAG Smoke run 112 SUCCESS | 3 条旧 review thread 均已解决；新 head 尚无 review submission | 分支基于旧 main；需等 #44 代码合并后同步并重验。凭据轮换/旧值失效未完成，P0 阻断放行 |
| 凭据安全治理 | #44 | 安全代码 head `5a3fb3b` 已推送，另有 5 脚本/示例/看板改动待提交；PR open、mergeable，尚未合并 | 旧 head run 139 SUCCESS；最新 worktree tree scan 0 命中。5 脚本缺少配置均在网络前 fail closed，伪配置 `.env` + mock HTTP 验证 5/5 环境读取与登录数据 | GPT 指出的根 `.env` 加载问题已修复并 resolve；1/1 旧 thread 已解决 | 全 refs 历史扫描 67 条模式命中（值未输出，待路径级复核）；本机 ignored `.env` 与归档 `.env` 各 12 条规则命中。轮换/失效验证未做，P0 BLOCKED |
| RAG MultiQuery P0 | 新 PR | 未启动（依赖门禁未满足） | — | — | 等 PR #41/#42 合并及用户批准 |
| B1-C | #45 | head `d2f91a6` 已推送并同步 `b0f71bc`；PR open，mergeable | 本地隔离 PostgreSQL 定向集 `110 passed in 48.93s`；PR run 140 in progress。首次未传测试环境的运行未进入测试（PG 认证配置缺失）；另一次缺少开发态本地免鉴权参数造成既有 API 用例 503，补齐显式测试参数后全集通过 | 3/3 旧 GPT review thread 已解决；同步后 head 尚无新 review submission | 当前 `register_all()` 仅注册 4 个内置 Workflow，均已设总预算；未臆造第 5 个。物理 Provider 未真实调用；CI 完成前不列入合并清单 |
| B1-D | #47 | head `b0a1800` 已推送并同步 `b0f71bc`；PR open，mergeable | 最新 base 定向集 `134 passed in 12.65s`；PR run 141 in progress。使用 fake session/repository 与 Fake Redis，未连接共享 PostgreSQL、Redis 或 Provider | 2/2 旧 GPT review thread 已解决；同步后 head 尚无新 review submission | PostgreSQL CAS 集成用例仍未运行，避免操作共享数据库；CI 完成后再复核合并条件 |
| B1-E | 新 PR | 只读核对；尚未施工 | — | — | 生产实例的 Checkpointer 实际运行状态无可读证据；按门禁暂停实现 |

## 已取得证据

- PR #40 head `9cca719`：按用户批准通过 GitHub 正常 merge 流程合入 main，merge commit `ed28db1`（父提交为 `5e48246` 与 PR head `9cca719`），未改写历史。合并前 run 111 的 RAG Smoke SUCCESS、评测 job 按路径 SKIP、Detect SUCCESS；0 条未解决 inline review thread。合并后在独立干净 Worktree、隔离本地 PostgreSQL 集群运行 `backend/tests/orchestration/workflow/test_selection_decision_event_loop.py` 与 `test_selection_decision_smoke.py`，`11 passed in 24.66s`；merge SHA 没有触发 main workflow/check run。主工作树原有用户修改未动。排队上限/等待超时不在本 PR 中扩展。
- PR #42 原 head `fb57241` 曾为 `58 passed, 2 skipped, 9 xfailed`；同步 #40/main 后 head `d0223b9` 的组合集为 `65 passed, 9 xfailed`，run 134 的 RAG Smoke、Evaluation、Detect 全部 SUCCESS。
- PR #43 原 head `2172208` 曾为 `28 passed`；同步 #40/main 后 head `e619ff1` 的组合集为 `39 passed`，run 135 的 RAG Smoke、Evaluation、Detect 全部 SUCCESS。
- PR #41 head `fc27dec`（基于当时最新 `a67449b`）：RAG 定向回归 `81 passed in 14.05s`；PR 的 `secret-scan` run 8 SUCCESS，RAG Smoke run 112 SUCCESS。前轮真实 Provider/性能/准确率证据未重跑、原样保留。
- PR #41 当前树仍报告根 `.env.example` 与 3 个 Travel 脚本的 4 项 `assigned_secret`；安全 PR #44 中已修正这些代码项，因此 PR #41 需等 #44 合并后再同步并重扫。PR #41 的 `origin/main..HEAD` 可达提交区间扫描为 0；历史讨论记录此前曾清理 PR 分支历史。本任务未重写 main 或其他共享历史。
- 安全 PR #44 的上一个推送 head `5a3fb3b`：run 139 SUCCESS；本地 tree scan 0。随后复核发现 Travel 3 脚本的登录账号为固定测试字段，另有 `context_budget_verify.py`、`dgroup_sse_test.py` 静态登录字段；5 个脚本现统一从根 `.env`/进程环境读取 `API_KEY`、`TEST_USERNAME`、`TEST_PASSWORD`，缺配置在网络前退出。使用临时全假配置、mock `urlopen` 与内存文件，5/5 通过配置加载及登录数据验证；无真实 Provider/服务调用。缺 API_KEY 与缺 TEST 登录配置两种情况下均 5/5 fail closed。`py_compile` 与 `git diff --check` 通过。新 scanner tree scan 0；diff/history 待提交后复扫。
- 安全扫描范围补查：本机根 `.env` 和 `.worktree-cleanup-archive-20261010` 的 235 个可读文件中，根 `.env` 与一个归档 `.env` 各有 12 条规则命中；值未输出，是否为有效凭据尚未验证。归档与根 `.env` 是用户工作树数据，未改动。全部 Git refs 历史扫描发现 67 条敏感模式命中，值未输出，待通过路径/规则清单复核并区分旧 Redis 暴露与其他存量；不改写 main 历史。P0 不得关闭。
- PR #42 已同步 main（含 #40）至 head `d0223b9` 并按用户批准通过 GitHub 正常 merge 流程，merge commit `0c471fb`（父提交 `ed28db1` 与 `d0223b9`）。合并前 RAG PR Smoke、Evaluation、Detect 全部 SUCCESS。合并后在 detached worktree `D:/tmp/agent-pr42-postmerge-20261012`、隔离本地 PostgreSQL 集群，重跑 Eval、LLM governance、Travel intent 与 B1-A 回归集，`65 passed, 9 xfailed in 31.17s`；无真实 Provider 调用。merge SHA 没有 GitHub Actions run/commit status；未把 PR head CI 冒作 merge SHA CI。
- PR #43 已同步 main `0c471fb` 至 head `6b1c32a`，并经单独批准通过 GitHub 正常 merge 流程合入 main，merge commit `b0f71bc`。run 138 的 Detect、RAG Smoke Evaluation、RAG Smoke 全部 SUCCESS。合并后在 detached worktree `D:/tmp/agent-pr43-postmerge-20261012`、隔离本地 PostgreSQL 集群重跑终态取消、错误协议与 B1-A 选择工作流定向集，`39 passed in 35.46s`；无真实 Provider 调用。merge SHA 没有 GitHub Actions run/commit status；未把 PR head CI 冒作 merge SHA CI。原 2 条 GPT review thread 已解决。
- B1-C PR #45 当前 head `d2f91a6` 已同步 `b0f71bc` 并推送；Workflow / DirectExecutor / RequestContext / SafeToolExecutor / B1-A 相关定向集在隔离 PG 上 `110 passed in 48.93s`。全套显式设置本地 `ALLOW_UNAUTHENTICATED=true` 与 `ENVIRONMENT=development`，不调用真实 Provider。deadline 取消单测单独验证也通过；首次误用默认 PG 端口运行导致环境错误已记录。PR run 140 正在执行；3 条旧 review thread 已 resolve，最新 head 未复审。
- B1-D PR #47 当前 head `b0a1800` 已同步 `b0f71bc` 并推送；Prompt Service、Release Service、Hot Reload、Prompts API、Release API、Prompt Trace 定向集 `134 passed in 12.65s`。`git diff --check` 通过；测试使用 fake session/repository 与 Fake Redis，不连接共享 PostgreSQL、真实 Redis 或 Provider。PostgreSQL CAS 真库用例未运行。PR run 141 正在执行；2 条旧 review thread 已 resolve，最新 head 未复审。
- B1-E 只读核对：仓库 Docker Compose 的 Travel Checkpointer 默认值为 PostgreSQL 开启，应用配置的默认值则为关闭；运行时图状态区分 `healthy/degraded/disabled`，生产启动对缺少 PostgresSaver 依赖有显式校验。现有 interrupt 测试使用 `MemorySaver`，不能证明生产配置。当前工作区没有生产部署配置读取或实例运行状态访问，故不能确认实际生产后端与 interrupt 开关；未修改 B1-E 代码，需取得不含凭据的生产配置/运行状态证据后再施工。
- 风险复核发现 Travel 脚本中的登录字段符合扫描器的测试占位符启发式；不记录字段值，也未证明对应账号在生产不可用。三个 Travel 脚本现也改为从环境读取登录字段。PR #41 已增加短密码规则与回归测试，旧 CI 门禁待新 head 执行。
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

1. PR #40/#42/#43 已依序按用户批准合入并完成各自 post-merge 定向验收。#44/#45/#47 已同步至 `b0f71bc`；#45 run 140、#47 run 141 待完成。
2. 凭据持有人授权后完成 Redis 与 API Key 轮换、部署更新、撤销旧值及旧值认证失败验证；确认测试登录常量仅供隔离环境使用。此前 P0 BLOCKED。
3. 提交 PR #44 五个验证脚本的凭据整改与看板，等待新 head CI；继续复核 67 条历史命中但绝不输出值。然后提交 #44 合并清单并取得单独用户批准；凭据轮换/旧值失效验证前 P0 风险不关闭。#41 仍需 #44 代码合入后同步并重跑 Secret Scan 与 RAG 定向验收。
4. 只有 PR #41 和 #42 合并后才施工 RAG MultiQuery P0；B1-C/#45、B1-D/#47 等用户审批。B1-E 先取得生产 Checkpointer 实际状态证据，再独立施工。
