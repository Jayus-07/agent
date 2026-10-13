# 进展记录

更新时间：2026-10-14（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`d3272c96d252871a0b5de7e1537c85f3a4f278aa`（已包含 PR #40–#45、#47、#51；PR #41/#44 也已合并）。PR #52 基于该 main 打开，仍未合并。
- 安全治理工作树：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- #41 在 2026-10-12 已合并，merge commit `48f3d498`。#45/#47 分别以 `8f65712`、`c2feb44` 合并；#51 以当前 main `d3272c9` 合并。真实服务凭据轮换与旧值失效情况仍须单独确认。
- B1-E 工作树：`C:/Users/wh/.codex/worktrees/b1e-runtime-interrupt-resume-20261013/agent`，分支 `codex/b1e-travel-interrupt-resume-20261014`，基于 `d3272c9`。本机 Docker Compose 的 app/agent-worker Checkpointer 为 PostgreSQL 且运行态 healthy；本地 `TRAVEL_USER_DECISION_INTERRUPT` 未设置（默认关闭），`ENVIRONMENT` 未设置，故该证据不是生产配置证明。

## 统一看板

| 项目 | PR | 状态 | CI | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | 已合并至 main，merge commit `ed28db1` | PR head `9cca719` / RAG PR Smoke run 111 SUCCESS，评测 job 按路径 SKIP；合并后隔离 Worktree 定向集 11 passed，merge SHA 无 main workflow/check run | 0 条未解决 review thread；无 GPT review submission | 排队上限/等待超时继续挂账，不扩本 PR |
| B1-B | #42 | 已按用户批准合并至 main，merge commit `0c471fb` | 合并前 head `d0223b9` 的 RAG PR Smoke、Evaluation、Detect 全部 SUCCESS；合并后隔离 worktree 定向集 65 passed、9 xfailed；merge SHA 无 main workflow/check run | 3 条旧 review thread 已解决 | 已完成；Usage Attribution 与预算账本按 `tenant=eval` 对齐 |
| B1-F | #43 | 已按用户批准合并至 main，merge commit `b0f71bc` | 合并前 run 138 的 Detect、RAG Smoke Evaluation、RAG Smoke 全部 SUCCESS；合并后隔离 worktree 定向集 39 passed；merge SHA 无 main workflow/check run | 2 条旧 review thread 已解决 | 已完成；逻辑取消与物理 Provider 停止边界保持明确 |
| RAG R0/R1 | #41 | 已合并，merge commit `48f3d49` | 合并前 RAG 定向集 `134 passed`，Secret Scan 与 RAG Smoke 均通过 | 3 条旧 review thread 已解决 | 凭据历史命中不等于仍有效；Redis/API Key 轮换和旧值失效未完成，安全 P0 仍 BLOCKED |
| 凭据安全治理 | #44、#51 | 均已合并；#44 `73c37d7`，#51 `d3272c9` | #44 Secret Scan、RAG Smoke 通过；#51 PR 检查通过 | 已解决已知 review thread | 代码侧整改已合入；生产 Redis/API Key 轮换与旧值失效未知，安全 P0 仍 BLOCKED |
| RAG MultiQuery P0 | #52 | OPEN；head `63d317b`，base `d3272c9`，mergeable | 当前 run `38449989265`：RAG Smoke、preflight、Detect PASS；真实 API evaluation 按 PR 策略 SKIP；Secret Scan run `38449989269` PASS。此前唯一获授权真实评测的质量门失败，真实预算/用量对账仍未证实 | 当前 head 无已核实的新 GPT 审查结论 | Recall/多跳质量门与计费证据未达标；不得合并或重复付费重跑，等待修复/新授权后的证据 |
| B1-C | #45 | 已合并，merge commit `8f65712` | 合并前定向集 `140 passed`，RAG PR Smoke PASS；无真实 Provider 调用 | 3/3 旧 GPT review thread 已解决 | 已完成；验收按用户确认的 4 个注册 Workflow |
| B1-D | #47 | 已合并，merge commit `c2feb44` | 合并前定向集 `135 passed`，RAG PR Smoke、py_compile、Ruff、diff-check PASS | 2/2 旧 GPT review thread 已解决 | 已完成；未接生产 Redis/Provider |
| B1-E | #56 | OPEN；代码 commit `de5db4c`，最新已验看板 head `505a460`（其余仅任务板更新），base `d3272c9`，GitHub mergeable | run `38455757460`（head `505a460`）：RAG Smoke、Detect PASS；真实 API Evaluation 与 RAG preflight 按路径 SKIP；Secret Scan run `38455757456` PASS。代码定向回归 `76 passed, 5 deselected`；语法/聚焦 Ruff/diff-check PASS | 0 条 review thread；当前无 GPT review submission | 生产 Checkpointer/interrupt 配置未核实；本机 Docker Compose 仅证明 dev app 与 worker 的 PostgreSQL Checkpointer healthy、interrupt 默认关闭。生产运行验收阻断，不能据 PR CI 宣称生产启用 |

## 已取得证据

> 本节记录各阶段当时的证据；若其状态与上方 2026-10-14 看板不一致，以看板的最新 GitHub 查询为准。

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
- PR #44 head `b43f52a` 经用户单独批准，通过 GitHub 正常 merge 流程合入 main，merge commit `73c37d73c3b7c62711db276458912eed481ace7d`；合并前 main/base 为 `b0f71bc`、PR mergeable=true、旧 review thread 已解决，RAG PR Smoke run 147 SUCCESS。合并后 PR 状态 closed/merged；merge SHA 未触发 workflow run/commit status，故未报告 merge-SHA CI 通过。Travel 五个脚本的环境读取与 fail-closed 验证、Secret Scan 零命中证据均来自 PR head；无真实服务调用或凭据轮换。
- PR #41 在 #44 合并后的 base `73c37d7` 上已完成同步；RAG 定向集 `134 passed in 15.24s`，tree/diff/history Secret Scan 均 0。代码验证后只更新任务看板；产品代码 head `40c33e1` 未变化，后续验证 head `256d64f` 的 Secret Scan run 15、RAG PR Smoke run 154 均 SUCCESS；run 152 因新提交取消。未重跑真实 Provider；既有性能/召回证据保留。旧 review threads 3/3 已解决，无新 GPT review submission。实际 Redis/API Key 轮换和旧值失效验证尚未完成，安全 P0 继续 BLOCKED。
- B1-C/#45 同步 head `cdb1ebc` 与 base `73c37d7`：用户已确认按 4 个实际注册 Workflow 验收；定向集 140 passed in 52.23s，使用独立临时 PostgreSQL，未调用真实 Provider；RAG PR Smoke run 149 SUCCESS，mergeable=true。首轮因默认本机 PG 未提供测试库凭据而出现 34 个 setup error 和 1 个同因 E2E failure；同一测试集改用独立临时 PG 后通过，未把首轮失败冒充通过。
- B1-D/#47 同步 head `3e912d7` 与 base `73c37d7`：Prompt service/release/hot reload/API/trace 与 PostgreSQL CAS 定向集 135 passed in 14.10s，CAS 连接独立临时 PostgreSQL 并真实执行；未连接共享/生产 PostgreSQL、真实 Redis 或 Provider。RAG PR Smoke run 150 SUCCESS，py_compile/Ruff/diff-check 均通过，mergeable=true。
- B1-E 本轮在独立 Worktree `codex/b1e-travel-interrupt-resume-20261014` 实施了 V2 interrupt/resume、同身份 checkpoint 恢复、Postgres advisory lock、重复确认保护、Trace 版本/状态记录，并限制 degraded MemorySaver 不挂起用户请求。相关定向集 `76 passed, 5 deselected`；排除项在干净 `d3272c9` 基线同样因本机 PostgreSQL 认证配置失败。`py_compile`、`git diff --check` 与聚焦 Ruff 规则通过；无真实 Provider 调用。Compose 运行证据仅属本地开发环境：app/agent-worker 的 PostgreSQL Checkpointer healthy，人工中断开关未设置（默认关闭），`ENVIRONMENT` 未设置。生产运行配置未确认，不据此宣称生产可用。
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

1. B1-E PR #56 已建立；代码 head `de5db4c` 的定向验证通过，已验看板 head `505a460` 的 RAG Smoke/Detect/Secret Scan 通过。等待 GPT 审查与只读的生产 Checkpointer/interrupt 运行态证据。未确认前不宣称生产启用或完成运行验收，也不合并。
2. PR #52 的真实评测质量门与预算/用量对账证据未满足；既有一次真实评测已用完用户授权，不重跑、不合并，等待修复及新的明确授权。
3. Redis/API Key 真实凭据轮换和旧值失效验证仍未完成；代码 PR 已合并不等于安全 P0 关闭。用户指示开发阶段先不处理，等用户后续授权凭据操作。
4. RAG R2/R3 与 Runtime 资源生命周期治理不在本轮启动，等待上述 P0 门禁通过后再开独立任务。
