# 进展记录

更新时间：2026-10-12（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`a67449b07a2bc9b25ba61b01c9b336d4512842f9`。
- 安全治理工作树：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- 本任务未改写 main 历史；三个已有 PR 的分支均已同步当前 main。

## 统一看板

| 项目 | PR | 状态 | CI | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | `READY_TO_MERGE` 标签已加 | head `9cca719` / RAG PR Smoke run 111 SUCCESS | 0 条未解决 review thread | 等用户批准合并；排队上限/等待超时继续挂账 |
| B1-B | #42 | 修复已推送 | head `fb57241` / RAG PR Smoke run 110 SUCCESS | 3 条旧 review thread 已解决；修复来自旧 head 审查，未发现针对当前 head 的新审查 | 等用户批准合并 |
| B1-F | #43 | 修复已推送 | head `2172208` / RAG PR Smoke run 109 SUCCESS | 2 条旧 review thread 已解决；修复来自旧 head 审查，未发现针对当前 head 的新审查 | 等用户批准合并 |
| RAG R0/R1 | #41 | 最新 main 上最终核对完成，未合并 | head `fc27dec` / Secret Scan run 8 SUCCESS；RAG PR Smoke run 112 SUCCESS | 3 条旧 review thread 均已解决；无未解决 thread | 独立安全 PR #44 尚未合并；凭据轮换/旧值失效验证未完成，P0 仍阻断放行 |
| 凭据安全治理 | #44 | 代码整改已推送，head `5bf88b8` | RAG PR Smoke run 117 SUCCESS；head tree/diff/history 扫描均 0 命中 | GPT 指出的根 `.env` 加载问题已修复并 resolve；无未解决 thread | Redis/API Key 轮换和旧凭据失效验证需凭据持有人授权；未完成前 P0 BLOCKED |
| RAG MultiQuery P0 | 新 PR | 未启动（依赖门禁未满足） | — | — | 等 PR #41/#42 合并及用户批准 |
| B1-C | #45 | 实现与三项 GPT 审查修复已推送，head `8bd8ea2` | RAG PR Smoke run 118 运行中 | 父 Trace 所有权、物理完成未知时重试、checkpoint deadline 归一化三项已修复并 resolve；审查基于旧 head，尚无新 head 复审 | 等 run 118 完成；请求上下文里的第 5 个 Workflow 在当前唯一注册表不存在，未臆造新增 |
| B1-D | 新 PR | 未启动 | — | — | 按 Runtime 阶段依赖推进 |
| B1-E | 新 PR | 未启动 | — | — | 按 Runtime 阶段依赖推进 |

## 已取得证据

- PR #40 head `9cca719`：在最新 main 上运行 `backend/tests/orchestration/workflow/test_selection_decision_event_loop.py` 与 `test_selection_decision_smoke.py`，`11 passed in 121.91s`；GitHub run 111 SUCCESS，PR `mergeable=true`；已按用户要求加 `READY_TO_MERGE` 标签，未合并。排队上限/等待超时不在本 PR 中扩展。
- PR #42 head `fb57241`：定向 Python 回归 `58 passed, 2 skipped, 9 xfailed`；包括 eval tenant 守卫、judge 线程上下文、正常/异常/取消、连续评测后普通请求恢复、预占/结算/释放、fallback 与熔断器隔离；`py_compile`、`git diff --check` 通过；run 110 SUCCESS。
- PR #43 head `2172208`：此前定向回归 `28 passed`；覆盖单一取消终帧、Timeout SSE/Trace、敏感异常不回传、通用异常 500；run 109 SUCCESS。
- PR #41 head `fc27dec`：同步最新 main 后，RAG 定向回归 `81 passed in 14.05s`；PR 的 `secret-scan` run 8 SUCCESS，RAG Smoke run 112 SUCCESS。前轮真实 Provider/性能/准确率证据未重跑、原样保留。
- PR #41 当前树仍报告根 `.env.example` 与 3 个 Travel 脚本的 4 项 `assigned_secret`；安全 PR #44 中已修正这些代码项，因此 PR #41 需等 #44 合并后再同步并重扫。PR #41 的 `origin/main..HEAD` 可达提交区间扫描为 0；历史讨论记录此前曾清理 PR 分支历史。本任务未重写 main 或其他共享历史。
- 安全 PR #44 head `5bf88b8` 使用 PR #41 的 `scripts/scan_secrets.py` 执行 `--tree`、`--diff origin/main..HEAD`、`--history --range origin/main..HEAD`，三项均 exit 0、0 命中。三个 Travel 脚本从项目根 `.env` 读取 `API_KEY`，进程环境变量优先；用不含真实凭据的临时 `.env` 做隔离行为验证：3/3 脚本均能读取配置，缺少配置时均在网络请求前失败关闭。`py_compile`、`git diff --check` 与 Ruff 导入排序检查通过。PR #44 run 117 SUCCESS；GPT P2 已修复并 resolve。没有真实 Provider 请求。
- B1-C PR #45 head `8bd8ea2`：Workflow / DirectExecutor / RequestContext / SafeToolExecutor 定向集 `99 passed`；Tool Deadline 定向集 `11 passed`。父 Trace 生命周期、取消吞噬后不重试、checkpoint dict Deadline 归一化新增覆盖；`py_compile`、`git diff --check` 通过。使用临时内存 fixture，无数据库与真实 Provider 调用。run 118 仍在运行，GPT 三条旧 head 线程均已 resolve。
- 风险复核另发现 Travel 脚本保留预置测试登录常量；未被 Secret Scan 识别为可验证凭据，当前用途似为测试账号，但是否只在隔离环境有效尚未确认。看板和 PR 未记录其值；需代码所有者确认非生产复用后才能消除此项风险。
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

1. 等 PR #45 run 118 完成，并刷新 PR #44 看板更新后的 CI。
2. 复核 PR #40–#45 的新 head、CI、review 和 mergeable；保持逐项合并，先给用户提交具体合并清单并等待批准。
3. 凭据持有人授权后完成 Redis 与 API Key 轮换、部署更新、撤销旧值及旧值认证失败验证；确认测试登录常量仅供隔离环境使用。此前 P0 BLOCKED。
4. PR #44 获批合并后，将 PR #41 同步最新 main 并复扫；只有 P0 风险处理满足放行要求且用户批准，才继续合并 RAG/Runtime PR。
5. 只有 PR #41 和 #42 合并后才施工 RAG MultiQuery P0；B1-C 后续按依赖推进 B1-D、B1-E，各建独立 PR。
