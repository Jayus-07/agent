# 进展记录

更新时间：2026-10-12（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`b0f71bc93727279780c68ea6334c9fe6f4731abf`（PR #40/#42/#43 已按用户批准依次合并）。
- 安全治理工作树：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- #41 等待 #44 合入后再同步 main；#45/#47 当前均基于 `b0f71bc`。合并均使用 GitHub 正常 PR 流程，不改写 main 历史；每项合并前重新 fetch 并重验。

## 统一看板

| 项目 | PR | 状态 | CI | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | 已合并至 main，merge commit `ed28db1` | PR head `9cca719` / RAG PR Smoke run 111 SUCCESS，评测 job 按路径 SKIP；合并后隔离 Worktree 定向集 11 passed，merge SHA 无 main workflow/check run | 0 条未解决 review thread；无 GPT review submission | 排队上限/等待超时继续挂账，不扩本 PR |
| B1-B | #42 | 已按用户批准合并至 main，merge commit `0c471fb` | 合并前 head `d0223b9` 的 RAG PR Smoke、Evaluation、Detect 全部 SUCCESS；合并后隔离 worktree 定向集 65 passed、9 xfailed；merge SHA 无 main workflow/check run | 3 条旧 review thread 已解决 | 已完成；Usage Attribution 与预算账本按 `tenant=eval` 对齐 |
| B1-F | #43 | 已按用户批准合并至 main，merge commit `b0f71bc` | 合并前 run 138 的 Detect、RAG Smoke Evaluation、RAG Smoke 全部 SUCCESS；合并后隔离 worktree 定向集 39 passed；merge SHA 无 main workflow/check run | 2 条旧 review thread 已解决 | 已完成；逻辑取消与物理 Provider 停止边界保持明确 |
| RAG R0/R1 | #41 | head `4e2c247`，PR open、mergeable，未合并；base 仍是旧 main `a67449b` | 新增扫描器定向集 `10 passed`；secret-scan run 10 SUCCESS、RAG PR Smoke run 144 SUCCESS；原真实 Provider/性能/准确率证据保留 | 3 条旧 review thread 均已解决；当前 head 尚无新 review submission | 等 #44 代码合入后同步最新 main、重跑 Secret Scan 与 RAG 定向回归；全 refs 有 83 个历史模式命中待凭据持有人确认。轮换/旧值失效未完成，P0 阻断放行 |
| 凭据安全治理 | #44 | 最新 head `b98cc4e`，PR open、mergeable，尚未合并 | 最新 RAG PR Smoke run 146 SUCCESS；5 脚本 `py_compile` 通过；缺配置两种场景均 5/5 网络前 fail closed；全假 `.env` + mock HTTP 验证配置及登录字段 5/5；最新 tree/diff/history-range scan 均 0 | GPT 指出的根 `.env` 加载问题已修复并 resolve；1/1 旧 thread 已解决；无新 review submission | 最新 `origin/main` tree 有 6 个模式命中，#44 head tree 为 0。全 refs 扫描为 83 个模式命中/27 路径（值未输出）；本机根与归档 `.env` 各 12 个命中。真实凭据轮换、撤销和旧值失效验证未做，P0 BLOCKED |
| RAG MultiQuery P0 | 新 PR | 未启动（依赖门禁未满足） | — | — | 等 PR #41/#42 合并及用户批准 |
| B1-C | #45 | head `d2f91a6` 已推送并同步 `b0f71bc`；PR open、mergeable，未合并 | 隔离 PostgreSQL 定向集 `110 passed in 48.93s`；PR run 140 SUCCESS；无真实 Provider 调用 | 3/3 旧 GPT review thread 已解决；当前 head 尚无新 review submission | 用户确认按当前 4 个注册 Workflow 验收；等待单独合并批准 |
| B1-D | #47 | head `b0a1800` 已推送并同步 `b0f71bc`；PR open、mergeable，未合并 | 定向集 `134 passed in 12.65s`；隔离 PostgreSQL CAS 用例 `1 passed in 3.75s`；PR run 141 SUCCESS；CAS pytest 进程退出时另有 Proactor cleanup warning | 2/2 旧 GPT review thread 已解决；当前 head 尚无新 review submission | 未连接真实 Redis/Provider；cleanup warning 尚未确认归属。等待用户合并批准 |
| B1-E | 新 PR | 已核对源码配置和测试，尚未施工 | — | — | B1-C/#45、B1-D/#47 尚未合入；生产 Checkpointer 的实际运行状态无可读证据，需满足前序门禁并取得运行态配置 |

## 已取得证据

- PR #40 head `9cca719`：按用户批准通过 GitHub 正常 merge 流程合入 main，merge commit `ed28db1`（父提交为 `5e48246` 与 PR head `9cca719`），未改写历史。合并前 run 111 的 RAG Smoke SUCCESS、评测 job 按路径 SKIP、Detect SUCCESS；0 条未解决 inline review thread。合并后在独立干净 Worktree、隔离本地 PostgreSQL 集群运行 `backend/tests/orchestration/workflow/test_selection_decision_event_loop.py` 与 `test_selection_decision_smoke.py`，`11 passed in 24.66s`；merge SHA 没有触发 main workflow/check run。主工作树原有用户修改未动。排队上限/等待超时不在本 PR 中扩展。
- PR #42 原 head `fb57241` 曾为 `58 passed, 2 skipped, 9 xfailed`；同步 #40/main 后 head `d0223b9` 的组合集为 `65 passed, 9 xfailed`，run 134 的 RAG Smoke、Evaluation、Detect 全部 SUCCESS。
- PR #43 最终 head `6b1c32a` 按用户批准合入，merge commit `b0f71bc`；run 138 的 Detect、RAG Smoke Evaluation、RAG Smoke 全部 SUCCESS；合并后隔离 PostgreSQL 定向集 `39 passed`。
- PR #41 head `fc27dec`（基于当时最新 `a67449b`）：RAG 定向回归 `81 passed in 14.05s`；PR 的 `secret-scan` run 8 SUCCESS，RAG Smoke run 112 SUCCESS。前轮真实 Provider/性能/准确率证据未重跑、原样保留。
- 以 PR #41 新规则扫描最新 main `b0f71bc`，当前树共 6 处：根 `.env.example`、三个 Travel 验收脚本及两个手工验证脚本。安全 PR #44 已改为环境配置读取，当前 head tree 为 0；PR #41 仍需等 #44 合并后同步并重扫。其分支可达提交区间扫描为 0；本任务未重写 main 或其他共享历史。
- 安全 PR #44 head `077b12c` 完成 5 个脚本凭据读取整改：Travel 三个验收脚本及两个手工验证脚本，从根 `.env`/进程环境读取 `API_KEY`、`TEST_USERNAME`、`TEST_PASSWORD`，进程环境优先，缺配置在网络前退出。临时全假配置、mock `urlopen` 与内存文件验证配置读取/登录字段 5/5；缺 API_KEY 与缺测试登录配置均 5/5 fail closed；无真实 Provider/服务调用。`py_compile` 与 `git diff --check` 通过。Secret Scan 对 #44 head `077b12c` 的 tree/diff/base-range history 为 0；最新文档 head `b98cc4e` 再跑 tree/diff/history-range 均 0，RAG PR Smoke run 146 SUCCESS。
- 全 refs 历史扫描采用 PR #41 现有规则；官方扫描器与批量 `git cat-file --batch` 读取方式在 `origin/main..HEAD` 的对照结果均为 0。完整范围覆盖本地与 remote-tracking 分支以及 GitHub PR 1–47 的 head refs、存在的 merge refs，共 39,004 个对象：83 个对象/路径/规则命中，跨 27 路径（`assigned_secret` 62、`hardcoded_password` 16、`url_with_credentials` 5）。命中清单只保留 SHA、路径、规则类型，不含内容；同一个旧占位配置的多版会重复计数，不能据此认定 83 个真实凭据。
- 安全扫描范围补查：以 PR #41 新增规则扫描最新 `origin/main=b0f71bc`，工作树命中 6 处：根 `.env.example`、三个 Travel 验收脚本，以及 `context_budget_verify.py`、`dgroup_sse_test.py` 两个验证脚本；PR #44 当前 tree/diff/history-range 均 0。全 refs 83 个模式命中是历史风险信号，不是 83 个已确认有效凭据。根 `.env` 和 235 个可读归档文件中的一个 `.env` 各有 12 条命中；值未输出、活动状态未验证。根 `.env` 与归档均为用户工作树数据，未修改。未改写 main 历史；P0 不得关闭。
- PR #42 已同步 main（含 #40）至 head `d0223b9` 并按用户批准通过 GitHub 正常 merge 流程，merge commit `0c471fb`（父提交 `ed28db1` 与 `d0223b9`）。合并前 RAG PR Smoke、Evaluation、Detect 全部 SUCCESS。合并后在 detached worktree `D:/tmp/agent-pr42-postmerge-20261012`、隔离本地 PostgreSQL 集群，重跑 Eval、LLM governance、Travel intent 与 B1-A 回归集，`65 passed, 9 xfailed in 31.17s`；无真实 Provider 调用。merge SHA 没有 GitHub Actions run/commit status；未把 PR head CI 冒作 merge SHA CI。
- PR #43 已同步 main `0c471fb` 至 head `6b1c32a`，并经单独批准通过 GitHub 正常 merge 流程合入 main，merge commit `b0f71bc`。run 138 的 Detect、RAG Smoke Evaluation、RAG Smoke 全部 SUCCESS。合并后在 detached worktree `D:/tmp/agent-pr43-postmerge-20261012`、隔离本地 PostgreSQL 集群重跑终态取消、错误协议与 B1-A 选择工作流定向集，`39 passed in 35.46s`；无真实 Provider 调用。merge SHA 没有 GitHub Actions run/commit status；未把 PR head CI 冒作 merge SHA CI。原 2 条 GPT review thread 已解决。
- B1-C PR #45 当前 head `d2f91a6` 已同步 `b0f71bc` 并推送；Workflow / DirectExecutor / RequestContext / SafeToolExecutor / B1-A 相关定向集在隔离 PG 上 `110 passed in 48.93s`。显式设置本地 `ALLOW_UNAUTHENTICATED=true` 与 `ENVIRONMENT=development`，不调用真实 Provider。deadline 取消单测单独验证通过；PR run 140 SUCCESS。源码仅有 4 个注册 Workflow；用户已确认按这 4 类验收，未新增虚构工作流。
- B1-D PR #47 当前 head `b0a1800` 已同步 `b0f71bc` 并推送；Prompt Service、Release Service、Hot Reload、Prompts API、Release API、Prompt Trace 定向集 `134 passed in 12.65s`；隔离 PostgreSQL CAS 用例 `1 passed in 3.75s`。`py_compile`、Ruff、`git diff --check` 通过；没有连接共享 PostgreSQL、真实 Redis 或 Provider。PR run 141 SUCCESS。CAS pytest 成功后 Windows 进程退出有 asyncio Proactor cleanup warning，归属未确认。
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

1. PR #40/#42/#43 已依序按用户批准合入并完成各自 post-merge 定向验收。#44/#45/#47 当前 head 均基于 `b0f71bc`；#44 最新 head run 146、#45 run 140、#47 run 141 均成功。
2. 凭据持有人授权后完成 Redis 与 API Key 轮换、部署更新、撤销旧值及旧值认证失败验证；确认测试登录常量仅供隔离环境使用。此前 P0 BLOCKED。
3. PR #44 五个验证脚本、示例和统一看板已提交至 head `b98cc4e`，run 146 SUCCESS。全 refs 扫描完成：83 个模式命中/27 路径，需凭据持有人确认潜在活凭据并执行 Redis/API Key 轮换、旧值撤销及失效验证；完成前 P0 BLOCKED。随后提交 #44 合并清单并取得单独用户批准。#41 仍需 #44 代码合入后同步并重跑 Secret Scan 与 RAG 定向验收。
4. 只有 PR #41 和 #42 合并后才施工 RAG MultiQuery P0；B1-C/#45、B1-D/#47 等用户审批。B1-E 先取得生产 Checkpointer 实际状态证据，再独立施工。
