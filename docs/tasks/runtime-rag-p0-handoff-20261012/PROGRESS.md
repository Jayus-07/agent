# 进展记录

更新时间：2026-10-14（Asia/Shanghai）

## 当前基线与工作树

- 最新 origin/main：`888738365666706488492c90ee8b97d87b173d02`（PR #56 合并提交；已包含 #30、#40–#45、#47、#51、#56）。最近一次 main 真实 RAG Smoke baseline run 38397152826 在前一 main `2d1d0b1` 执行，Top-1 0.7143，低于 0.80 门槛；其后无新的 main 真实 RAG 评测。
- MultiQuery P0：`C:/Users/wh/.codex/worktrees/rag-multi-query-p0-20261013/agent`，分支 `codex/rag-multi-query-p0-20261013`。
- 凭据安全：`C:/Users/wh/.codex/worktrees/credential-security-p0-20261012/agent`，分支 `codex/credential-security-p0-20261012`。
- B1-E PR #56 已以 merge commit `8887383` 合入；合并后后端定向集 `78 passed, 5 deselected`、前端 `3 passed`、TypeScript 通过。生产 Checkpointer 配置仍未核实，本机 Docker 证据仅属开发环境。
- 用户主工作树原有改动未触碰；没有改写共享 Git 历史；不自动合并 PR。

## 统一看板

| 项目 | PR | 状态 | CI / 定向验证 | GPT 审查 | 阻断原因 |
| --- | --- | --- | --- | --- | --- |
| B1-A | #40 | 已合并，`ed28db1` | run 111 SUCCESS；合并后 11 passed | 无未解决 thread | 排队上限/等待超时继续挂账 |
| B1-B | #42 | 已合并，`0c471fb` | RAG Smoke/Evaluation/Detect SUCCESS；合并后 65 passed、9 xfailed | 3 条旧 thread 已解决 | 无 |
| B1-F | #43 | 已合并，`b0f71bc` | run 138 全绿；合并后 39 passed | 2 条旧 thread 已解决 | 无 |
| RAG R0/R1 | #41 | 已合并，`48f3d49` | run 166 RAG Smoke、run 17 Secret Scan SUCCESS；合并前本地 142 passed | 3 条旧 thread 已解决 | 无；既有性能/准确率证据保留 |
| 凭据安全治理 | #51 | 已合并，merge `d3272c9` | PR run 38447092565 RAG PR Smoke、38447092851 Secret Scan SUCCESS；合并后相同 tree 定向集 `test_startup_validation.py` 55 passed；post-merge combined status 未返回 status checks | 合并前 GPT 复审无未解决 thread | 本机开发 Redis 与 PostgreSQL 只读口令已按本轮授权轮换，新值认证通过，受影响容器 healthy；现存 `.env` 备份中的 PostgreSQL 候选旧口令认证拒绝。生产配置、API Key 与 PR #41 历史泄漏凭据仍未轮换/撤销，安全 P0 未关闭 |
| RAG MultiQuery P0 | #52 | OPEN；基于最新 main `8887383`；head `c1bbbcb` | RAG PR Smoke run `38480551639`、Secret Scan run `38480551604` SUCCESS；定向测试 `53 + 42 + 5 passed`，py_compile/YAML/模拟账本/Secret Scan 通过；PR 的真实 Provider job 按配置跳过 | 10 条旧 thread 已解决；当前 head 未有新 GPT 复审 | 唯一授权的真实 Provider run `38445344364` 严格 RAG gate FAILED；Recall@5 `0.881`、Multi-hop `0.7222`，低于基线 `0.9286/0.8333`；当前 head 的真实预算账本和召回质量仍未验证，重跑须新授权 |
| B1-C | #45 | 已合并，`8f65712` | run 163 SUCCESS；隔离 PostgreSQL 定向集 273 passed | 旧 thread 已解决 | 无；按用户要求以当前 4 类 Workflow 验收 |
| B1-D | #47 | 已合并，`c2feb44` | run 165 SUCCESS；定向集 135 passed + 隔离 PostgreSQL 6 passed | 2 条旧 thread 已解决 | 无 |
| B1-E | #56 | 已合并，merge commit `8887383` | Secret Scan run `38460502481`、RAG PR Smoke run `38460502482` 成功；合并后后端 `78 passed, 5 deselected`、前端 `3 passed`、TypeScript 通过 | GPT P2 1/1 已解决 | 本机 Docker app/worker PostgreSQL Checkpointer healthy，但 `ENVIRONMENT` 未设置；生产 Checkpointer/interrupt 运行配置未核实，不宣称生产验收 |

## MultiQuery P0 本批实施

- `multi_query` 改写通过 `bind_llm_for_role` 进入 LLMProxy；空角色依注册表继承 main，并使用原温度/token 上限。
- 改写线程提交时复制 ContextVar，确保 tenant/user/trace、限流身份和请求预算一致传播。
- Proxy 消费角色 timeout、重试和 skip 策略；默认失败不调用裸全局 LLM 或备用模型，按规则回退原始 query。
- 等待超时会取消尚未启动的线程池任务；已运行的 Provider 请求无法被线程取消，会按 Proxy 的用量/预算语义留痕。
- 同步/异步 primary 与 retry 都在预算预占返回后复查取消信号；若请求已超时，释放未执行预占并阻止 Provider 调用。
- 改写 Provider 调用改用 thread_pools 中独立、可观测的 multi_query_rewrite 池，慢请求不再占用 outer/inner 检索 worker。
- 失败日志只记录异常类型；R0/R1 Embedding 去重和缓存实现未改动。
- Eval case timeout/concurrency/RAGAS 线程池此前未复制 ContextVar；新增提交辅助函数后，tenant、user、Trace、budget request id 和 LLMProxy per-user rate-limit user 都进入工作线程。
- 评测作用域现在临时绑定 LLMProxy 限流 user，并在正常、异常和取消后恢复。Provider 用量缺失或价格标为 `unpriced` 时，Proxy 与 cloud TokenTracker 不再按 ¥0 结清预占，而转入 `needs_review` 保留占额。
- 可信 RAG Smoke job 设置 `LLM_BUDGET_MODE=enforce` 和独立 run id；artifact 以运行前 usage id 为边界，报告租户归属、预占/结算/释放/待对账金额及差额。无调用、错租户、遗漏 reservation、未终态 reservation 或金额不匹配均为 `UNVERIFIED`。

### MultiQuery 验证证据

- 当前代码 commit 4027387 定向回归：infra/test_b1b_llm_governance.py、test_multi_query_token_budget.py、test_retrieval_executor_isolation.py、test_multi_query_trace.py、test_retrieval_deadline.py，55 passed；另对部分成功超时场景锁定告警中的已完成文档数；py_compile 与 git diff --check 通过。
- 新增并发上下文用例验证两个 eval tenant/user 的 trace、预算 request id 和 LLMProxy 限流身份不串账；评测治理回归覆盖异常、取消、连续评测后普通请求恢复。
- 内存 quota-store 回归验证缺用量和 `unpriced` 不会零元结算，改为 `needs_review`；cloud TokenTracker 缺用量走相同待对账路径。
- 工作流 artifact 用模拟账本执行实际内嵌 Python 代码，检查 reservation 状态、预占/结算金额与差额；未调用 Provider 或 PostgreSQL。`rag_smoke.yml` YAML 解析通过。
- 本地取消与预算测试覆盖预占期间取消、同步/异步 Provider 阻断、未执行预占释放；真实 ThreadPoolExecutor 测试验证慢改写不能阻塞 outer 检索。
- 经用户授权仅执行一次真实 Provider run #38445344364（https://github.com/Jayus-07/agent/actions/runs/38445344364），head `7f1c454`、`multiquery=true`；8/8 case 级通过，实际 107 次 Provider 请求（chat 35、embedding 63、rerank 9），但严格评测门禁失败。
- 当前 run 的 Recall@5 0.881、MRR 0.8571、Top-1 0.7143、Multi-hop 0.7222；main 基线 run #38397152826 为 0.9286、0.8571、0.7143、0.8333。Top-1 两边都低于 0.80 门槛，没有改门槛或删案例。
- RC-024 的 trace 显示 3 层路由为 `hybrid`、rewrite `skipped`、0 variants；该 case Recall@5/Multi-hop 从基线的 1.0/1.0 变为 0.6667/0.6667。它没有实际走 MultiQuery 改写，单次 Provider 运行的变化不能归因为该功能，也不能据此声称总体召回未退化。
- 真正触发改写的长查询在 1.8 秒等待预算后回退原始 query；日志随后显示其 Provider 请求迟到完成并返回 1 个变体，调用方没有消费。该 case 指标与基线相同。当前证据确认请求发生，但费用为 `UNVERIFIED`，artifact 未提供 tenant/reservation/settlement 对账，无法证明预算账本与真实调用已对齐。
- R0/R1 Embedding 去重、缓存实现未改；现有 R0/R1 性能/准确率证据保留。

### 主线 RAG Smoke 门禁诊断

- baseline run `38397152826`（main `2d1d0b1`）与 #52 真实 Provider run `38445344364`（代码 head `7f1c454`）来自同一 `pr_smoke` 数据版本；前者 Recall@5/MRR/Top-1/Multi-hop 为 0.9286/0.8571/0.7143/0.8333，后者为 0.881/0.8571/0.7143/0.7222。main 后续只合并了 #51 凭据安全改动，没有新的 RAG 真实评测。
- 两次 run 都是 8/8 case 级通过，但 #52 的严格 gate 失败。已确认唯一出现 Recall@5/Multi-hop 差异的 RC-024 在 #52 trace 中没有触发改写；因此当前 live 对比既不能证明是 MultiQuery 导致，也不能作为未退化的验收证据。实际改写目标 RC-031 等待超时后回退，迟到的 Provider 输出未被消费；指标与 baseline 相同。
- 旧 head `63d317b` 的定向测试和 PR CI 通过，但真实费用核验为 `UNVERIFIED`，当时 workflow artifact 不含租户预算预占/结算账本核对。唯一已授权真实调用为 run `38445344364`，严格 RAG gate 失败；当前 head 尚未再调用 Provider。本地修复已增加待对账语义和运行级账本 artifact，当前 CI 已通过。
- PR #56/B1-E 已按用户批准合并（merge `8887383`）。新 main 上复跑 Travel 后端定向集为 `78 passed, 5 deselected`，V2 前端为 `3 passed`，TypeScript 检查通过；无真实 Provider 调用。5 个 PostgreSQL 配置相关排除项此前已在干净基线复现。本机 Docker app/worker 的 PostgreSQL Checkpointer healthy，但 `ENVIRONMENT` 未设置，不能证明生产配置。

## 凭据处置

- 本轮按用户授权重新轮换本机开发 Redis 与 PostgreSQL 只读凭据；`.env` 更新后，Redis、PostgreSQL 新认证通过，相关本机应用/Worker/Redis 容器全部 healthy，`.env` DACL 保持仅当前用户、SYSTEM 和 Administrators。当前 `.env` 备份中的 PostgreSQL 候选旧口令认证被拒绝；Redis 当前旧值未从环境备份恢复作直接回放，不能声称完成该项直接测试。
- 安全 PR #51 移除只读数据库配置和迁移中的弱默认值，并修复迁移已记账后的凭据状态漂移及共享集群跨库角色误禁用风险。head `ddc1301` 的 Secret Scan 与 RAG PR Smoke 均 SUCCESS；`test_startup_validation.py` 合并前后均 55 passed；scratch PostgreSQL 18 双数据库/SCRAM 验证目标库角色处理及另一库角色隔离；tree/history/diff Secret Scan 退出码 0；GPT 复审无未解决意见。PR 已按用户批准合并为 `d3272c9`；合并后 GitHub combined status 未返回 status checks，定向回归通过且合并树与已验收 head 的 tree hash 一致。
- Secret Scan 标准 `--tree`、`--history`、`--diff origin/main` 均为 0 命中。可选 `--with-runtime-secrets` 报 59 条命中，全部映射到非秘密配置 `JWT_ISSUER` 和 `TOKEN_USAGE_LOG_PATH`，不是凭据命中。
- API Key 和生产/外部 Secret Manager 未触碰，按用户要求维持开发阶段以外的配置。PR #41 既有历史泄露未做共享历史重写；历史处置需单独评估影响，不宣称凭据 P0 整体关闭。

## 下一步

1. #52 暂不合并：最新 free CI 与 Secret Scan 已通过；等待用户决定是否授权一次当前 head 的真实 Provider 评测以验证账本与质量。保留 0.80 Top-1 门槛；先前单次真实评测已使用，未重复调用。
2. 凭据安全代码已合入，本机开发 Redis/PostgreSQL 只读凭据已轮换；API Key、生产配置和 #41 历史泄漏凭据未轮换/撤销，不宣称安全 P0 关闭。
3. B1-E 已合并；生产 Checkpointer/interrupt 运行配置仍未知，部署前需由脱敏生产配置或只读运行证据确认。
4. RAG R2/R3 和 Runtime 资源生命周期治理按用户要求暂不启动。
