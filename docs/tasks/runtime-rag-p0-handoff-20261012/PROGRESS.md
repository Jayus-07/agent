# 进展记录

更新时间：2026-10-12（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`a67449b07a2bc9b25ba61b01c9b336d4512842f9`。
- 安全治理工作树：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- 本任务未改写 main 历史；三个已有 PR 的分支均已同步当前 main。

## 统一看板

| 项目 | PR | 状态 | CI | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | `READY_TO_MERGE` 标签已加 | head `9cca719` / RAG PR Smoke run 111 SUCCESS | 无未解决 review thread | 等用户批准合并；排队上限/等待超时继续挂账 |
| B1-B | #42 | 修复已推送 | head `fb57241` / RAG PR Smoke run 110 SUCCESS | 修复意见和 3 条旧 review thread 已解决；尚无新 head GPT 复审 | 等用户批准合并 |
| B1-F | #43 | 修复已推送 | head `2172208` / RAG PR Smoke run 109 SUCCESS | 修复意见和 2 条旧 review thread 已解决；尚无新 head GPT 复审 | 等用户批准合并 |
| RAG R0/R1 | #41 | 最新 main 已同步，做最终核对 | Secret Scan run 8 SUCCESS；RAG PR Smoke run 112 SUCCESS | 前轮三项 inline 意见已由当前代码修复并 resolve；此前第三轮接受主实现 | 等当前 RAG Smoke 完成；安全债脚本已在独立分支修复，待安全 PR 集成 |
| 凭据安全治理 | #44 | 已创建；代码提交 `c356047`，最近 head `2b821c6` | RAG PR Smoke run 114 SUCCESS；该 head tree/diff/history 扫描均 0 命中 | 待审查 | Redis/API Key 轮换和旧凭据失效验证需凭据持有人授权；未完成前 P0 BLOCKED |
| RAG MultiQuery P0 | 新 PR | 未启动（依赖门禁未满足） | — | — | 等 PR #41/#42 合并及用户批准 |
| B1-C | 新 PR | 独立工作树创建中，尚未改代码 | — | — | 正在核实与待合并 PR 的文件边界 |
| B1-D | 新 PR | 未启动 | — | — | 按 Runtime 阶段依赖推进 |
| B1-E | 新 PR | 未启动 | — | — | 按 Runtime 阶段依赖推进 |

## 已取得证据

- PR #40 head `9cca719`：在最新 main 上运行 `backend/tests/orchestration/workflow/test_selection_decision_event_loop.py` 与 `test_selection_decision_smoke.py`，`11 passed in 121.91s`；GitHub run 111 SUCCESS，PR `mergeable=true`；已按用户要求加 `READY_TO_MERGE` 标签，未合并。排队上限/等待超时不在本 PR 中扩展。
- PR #42 head `fb57241`：定向 Python 回归 `58 passed, 2 skipped, 9 xfailed`；包括 eval tenant 守卫、judge 线程上下文、正常/异常/取消、连续评测后普通请求恢复、预占/结算/释放、fallback 与熔断器隔离；`py_compile`、`git diff --check` 通过；run 110 SUCCESS。
- PR #43 head `2172208`：此前定向回归 `28 passed`；覆盖单一取消终帧、Timeout SSE/Trace、敏感异常不回传、通用异常 500；run 109 SUCCESS。
- PR #41 head `fc27dec`：同步最新 main 后，RAG 定向回归 `81 passed in 14.05s`；PR 的 `secret-scan` run 8 SUCCESS，RAG Smoke run 112 SUCCESS。前轮真实 Provider/性能/准确率证据未重跑、原样保留。
- PR #41 当前树扫描按已有 workflow 的阻断口径为 0 blocking；报告 4 个 `assigned_secret` 存量项（根 `.env.example` 与 3 个 Travel 脚本）。它们都已在安全工作树修正；此时 PR #41 分支仍以旧 main 为基线，待安全 PR 合并后再同步。对 PR #41 的 `origin/main..HEAD` 可达提交区间扫描为 0；历史讨论记录了 PR 分支此前曾重写以移除泄漏，但本任务未重写任何历史。
- 安全分支 commit `c356047` 使用 PR #41 `scripts/scan_secrets.py` 执行 `--tree`、`--diff origin/main..HEAD`、`--history --range origin/main..HEAD`，三项均 exit 0、0 命中。三个脚本通过 `py_compile`，在移除 `API_KEY` 环境变量的隔离进程中均在网络请求前退出并提示配置缺失。安全 PR #44 head `2b821c6` 的 RAG PR Smoke run 114 SUCCESS；该 head 的 tree/diff/history 扫描均为 0 命中。当前后续只更新看板文档。
- 安全分支另将根 `.env.example` 的 JWT 项改为明显占位符，避免把可复制的静态签名值当成配置。

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

1. 核验 PR #44 看板文档更新后的 CI；跟进 GPT 审查，不在 PR 正文或报告写入凭据值。
2. 轮询 PR #41 当前 RAG Smoke；复核四个 PR 的最新 CI、head、review 与 mergeable 状态。
3. 准备逐项合并清单，提交用户审批；在获批前不合并。
4. 安全 PR 合并后把 PR #41 同步到最新 main，确认树扫描无 Travel 存量项，再等待 #41/#42 合并与用户审批。
5. 只有 PR #41 和 #42 完成合并后，才从最新 main 独立施工 MultiQuery P0；B1-C/D/E 维持各自独立 PR。
