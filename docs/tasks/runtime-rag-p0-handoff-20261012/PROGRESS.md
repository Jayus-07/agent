# 进展记录

更新时间：2026-10-14（Asia/Shanghai）

## 当前基线与工作树

- 最新 origin/main：2d1d0b173f50128429d9c868301ee05b38bcd85b（#30 合并提交；已包含 #41、#45、#47）。主线 run 38397152826 的 RAG Smoke 失败：Top-1 0.7143，低于 0.80 门槛。
- MultiQuery P0：`C:/Users/wh/.codex/worktrees/rag-multi-query-p0-20261013/agent`，分支 `codex/rag-multi-query-p0-20261013`。
- 凭据安全：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- 用户主工作树原有改动未触碰；没有改写共享 Git 历史；不自动合并 PR。

## 统一看板

| 项目 | PR | 状态 | CI / 定向验证 | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | 已合并，`ed28db1` | run 111 SUCCESS；合并后 11 passed | 无未解决 thread | 排队上限/等待超时继续挂账 |
| B1-B | #42 | 已合并，`0c471fb` | RAG Smoke/Evaluation/Detect SUCCESS；合并后 65 passed、9 xfailed | 3 条旧 thread 已解决 | 无 |
| B1-F | #43 | 已合并，`b0f71bc` | run 138 全绿；合并后 39 passed | 2 条旧 thread 已解决 | 无 |
| RAG R0/R1 | #41 | 已合并，`48f3d49` | run 166 RAG Smoke、run 17 Secret Scan SUCCESS；合并前本地 142 passed | 3 条旧 thread 已解决 | 无；既有性能/准确率证据保留 |
| 凭据安全治理 | #51 | Open，head `ddc1301`；共享集群角色标记隔离已修复 | run 38447092565 RAG PR Smoke、run 38447092851 Secret Scan SUCCESS；`test_startup_validation.py` 55 passed；隔离 PostgreSQL 18 双数据库/SCRAM 验证当前库旧角色禁用、会话终止且另一数据库角色不受影响；tree/history/diff Secret Scan 退出码 0 | 对 `ddc1301` 的 GPT 复审已完成；无未解决 thread | 生产 `PG_READONLY_PASSWORD` 是否已受控配置尚未核实；Redis/API Key 与 PR #41 历史泄漏凭据按用户要求未轮换/撤销，安全 P0 未关闭 |
| RAG MultiQuery P0 | #52 | Open，代码 commit `4027387`；真实评测 head `7f1c454` | run 38440124676 RAG PR Smoke、run 38440124430 Secret Scan SUCCESS；定向集 55 passed；获授权的真实 Provider run 38445344364 完成 8/8 case，但严格 RAG gate FAILED | 代码 head `7f1c454` GPT review 已完成；无未解决 thread | 当前 run Recall@5 0.881、Multi-hop 0.7222，低于 main 实测 0.9286/0.8333；RC-024 本身未触发改写，不能归因为 MultiQuery；真实费用为 UNVERIFIED，artifact 无预算账本对账证据 |
| B1-C | #45 | 已合并，`8f65712` | run 163 SUCCESS；隔离 PostgreSQL 定向集 273 passed | 旧 thread 已解决 | 无；按用户要求以当前 4 类 Workflow 验收 |
| B1-D | #47 | 已合并，`c2feb44` | run 165 SUCCESS；定向集 135 passed + 隔离 PostgreSQL 6 passed | 2 条旧 thread 已解决 | 无 |
| B1-E | 新 PR | 尚未施工 | — | — | 仓库只找到开发 compose 的 Checkpointer 配置，缺生产实际启用证据；须先取得部署配置/运行证据 |

## MultiQuery P0 本批实施

- `multi_query` 改写通过 `bind_llm_for_role` 进入 LLMProxy；空角色依注册表继承 main，并使用原温度/token 上限。
- 改写线程提交时复制 ContextVar，确保 tenant/user/trace、限流身份和请求预算一致传播。
- Proxy 消费角色 timeout、重试和 skip 策略；默认失败不调用裸全局 LLM 或备用模型，按规则回退原始 query。
- 等待超时会取消尚未启动的线程池任务；已运行的 Provider 请求无法被线程取消，会按 Proxy 的用量/预算语义留痕。
- 同步/异步 primary 与 retry 都在预算预占返回后复查取消信号；若请求已超时，释放未执行预占并阻止 Provider 调用。
- 改写 Provider 调用改用 thread_pools 中独立、可观测的 multi_query_rewrite 池，慢请求不再占用 outer/inner 检索 worker。
- 失败日志只记录异常类型；R0/R1 Embedding 去重和缓存实现未改动。

### MultiQuery 验证证据

- 当前代码 commit 4027387 定向回归：infra/test_b1b_llm_governance.py、test_multi_query_token_budget.py、test_retrieval_executor_isolation.py、test_multi_query_trace.py、test_retrieval_deadline.py，55 passed；另对部分成功超时场景锁定告警中的已完成文档数；py_compile 与 git diff --check 通过。
- 本地取消与预算测试覆盖预占期间取消、同步/异步 Provider 阻断、未执行预占释放；真实 ThreadPoolExecutor 测试验证慢改写不能阻塞 outer 检索。
- 经用户授权仅执行一次真实 Provider run #38445344364（https://github.com/Jayus-07/agent/actions/runs/38445344364），head `7f1c454`、`multiquery=true`；8/8 case 级通过，实际 107 次 Provider 请求（chat 35、embedding 63、rerank 9），但严格评测门禁失败。
- 当前 run 的 Recall@5 0.881、MRR 0.8571、Top-1 0.7143、Multi-hop 0.7222；main 基线 run #38397152826 为 0.9286、0.8571、0.7143、0.8333。Top-1 两边都低于 0.80 门槛，没有改门槛或删案例。
- RC-024 的 trace 显示 3 层路由为 `hybrid`、rewrite `skipped`、0 variants；该 case Recall@5/Multi-hop 从基线的 1.0/1.0 变为 0.6667/0.6667。它没有实际走 MultiQuery 改写，单次 Provider 运行的变化不能归因为该功能，也不能据此声称总体召回未退化。
- 真正触发改写的长查询在 1.8 秒等待预算后回退原始 query；日志随后显示其 Provider 请求迟到完成并返回 1 个变体，调用方没有消费。该 case 指标与基线相同。当前证据确认请求发生，但费用为 `UNVERIFIED`，artifact 未提供 tenant/reservation/settlement 对账，无法证明预算账本与真实调用已对齐。
- R0/R1 Embedding 去重、缓存实现未改；现有 R0/R1 性能/准确率证据保留。

### 主线 RAG Smoke 门禁诊断

- 最新 `origin/main` 为 `2d1d0b173f50128429d9c868301ee05b38bcd85b`。baseline run `38397152826` 与当前代码头 `7f1c454` 的真实 Provider run `38445344364` 来自同一 `pr_smoke` 数据版本；前者 Recall@5/MRR/Top-1/Multi-hop 为 0.9286/0.8571/0.7143/0.8333，后者为 0.881/0.8571/0.7143/0.7222。
- 两次 run 都是 8/8 case 级通过，但 #52 的严格 gate 失败。已确认唯一出现 Recall@5/Multi-hop 差异的 RC-024 在 #52 trace 中没有触发改写；因此当前 live 对比既不能证明是 MultiQuery 导致，也不能作为未退化的验收证据。实际改写目标 RC-031 等待超时后回退，迟到的 Provider 输出未被消费；指标与 baseline 相同。
- #52 定向测试和 PR CI 通过，但真实费用核验为 `UNVERIFIED`，workflow artifact 不含租户预算预占/结算账本核对。此次为用户授权的唯一真实调用，不再重复付费；其后未改代码，仅更新文档。

## 凭据处置

- 已按用户授权轮换本机 Docker PostgreSQL 的只读角色口令：新口令从主机及 `app` 容器认证成功；旧口令被 PostgreSQL 拒绝；角色无建库权限，业务库无 CREATE 权限。
- 读取根目录 `.env` 的 9 个本地应用/Worker 容器已重建并全部 healthy；`.env` DACL 收紧到当前用户、SYSTEM 和 Administrators。口令值未写入日志、PR 或报告。
- 安全 PR #51 移除只读数据库配置和迁移中的弱默认值，并修复迁移已记账后的凭据状态漂移及共享集群跨库角色误禁用风险。head `ddc1301` 的 Secret Scan 与 RAG PR Smoke 均 SUCCESS；`test_startup_validation.py` 55 passed；scratch PostgreSQL 18 双数据库/SCRAM 验证目标库角色处理及另一库角色隔离；tree/history/diff Secret Scan 退出码 0；GPT 复审无未解决意见。
- Secret Scan 标准 `--tree`、`--history`、`--diff origin/main` 均为 0 命中。可选 `--with-runtime-secrets` 报 59 条命中，全部映射到非秘密配置 `JWT_ISSUER` 和 `TOKEN_USAGE_LOG_PATH`，不是凭据命中。
- Redis/API 当前值本轮未修改，按用户要求维持开发阶段现状；本地生产/外部 Secret Manager 均未触碰。PR #41 既有历史泄露未做共享历史重写；历史处置需单独评估影响，不宣称凭据 P0 整体关闭。

## 下一步

1. #51 CI 与 GPT 复审已完成；合并前须取得用户批准，部署前须确认生产 `PG_READONLY_PASSWORD` 受控配置。Redis/API Key 和 #41 历史泄漏凭据仍未轮换/撤销，不宣称安全 P0 关闭。
2. #52 保持不合并：先依据现有 trace/ledger 证据确认真实请求、迟到 Provider 调用的账务归因及预算结算；真实 RAG gate 的 RC-024 单次差异需查明来源。继续使用现有 0.80 Top-1 门槛，不增加真实 Provider 调用，除非取得新授权。
3. B1-E 继续等待生产 Checkpointer 的真实部署配置/运行证据；当前仓库只找到开发 compose 配置。
