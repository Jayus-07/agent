# 进展记录

更新时间：2026-10-14（Asia/Shanghai）

## 当前基线与工作树

- 最新 origin/main：`76334163a82f2fa9a969155da2800f7918476e5b`（PR #57 合并提交；已包含 #30、#40–#45、#47、#51、#56、#57）。PR #52 仍基于 `8887383`；PR #55 `c40ed5b` 当前 OPEN，等待其会话处理。最近一次 main 真实 RAG Smoke baseline run 38397152826 在前一 main `2d1d0b1` 执行，Top-1 0.7143，低于 0.80 门槛；其后无新的 main 真实 RAG 评测。
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
| RAG MultiQuery P0 | #52 | OPEN；代码 head `9f2574f`，PR base 仍为 `8887383` | 新 head run `38484509377` 免费 RAG Smoke SUCCESS、Secret Scan `38484509166` SUCCESS；定向测试 `10 + 59 passed`，py_compile 与 diff-check 通过；真实调用 job 在 PR CI 跳过 | 10 条旧 thread 已解决；`9f2574f` 新增 diff 尚无 GPT 复审 | 经授权的 run `38481681899`（评测 SHA `d7496f5`，`multiquery=true`）严格 gate FAILED：Top-1 `0.7143`；31 次调用有 22 次身份未归属，所有价格未知，费用/预算账本 `UNVERIFIED`。修复后的 head 未重新做真实评测；等待 PR #55 会话处理及合入后同步当前 main，并需新的付费评测授权 |
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

- 代码 commit `9f2574f` 修复真实评测发现的检索线程池身份丢失：`submit_rag_task` 在提交线程复制独立 ContextVar 快照，向量任务保留 tenant/user/trace 与请求预算；新增同线程池复用后的身份恢复断言。定向线程池/评测上下文 `10 passed`，MultiQuery Proxy/预算/Trace/LLM 治理 `59 passed`；py_compile 与 git diff --check 通过。
- 新增并发上下文用例验证两个 eval tenant/user 的 trace、预算 request id 和 LLMProxy 限流身份不串账；评测治理回归覆盖异常、取消、连续评测后普通请求恢复。
- 内存 quota-store 回归验证缺用量和 `unpriced` 不会零元结算，改为 `needs_review`；cloud TokenTracker 缺用量走相同待对账路径。
- 工作流 artifact 用模拟账本执行实际内嵌 Python 代码，检查 reservation 状态、预占/结算金额与差额；未调用 Provider 或 PostgreSQL。`rag_smoke.yml` YAML 解析通过。
- 本地取消与预算测试覆盖预占期间取消、同步/异步 Provider 阻断、未执行预占释放；真实 ThreadPoolExecutor 测试验证慢改写不能阻塞 outer 检索。
- 用户另行授权后，仅执行一次新增真实 Provider run `38481681899`（https://github.com/Jayus-07/agent/actions/runs/38481681899），事件 `workflow_dispatch`、评测 SHA `d7496f5`、`multiquery=true`。隔离库导入 100 份基准后索引完整；8/8 case 检查通过，但严格 gate 因 Top-1 低于 0.80 而失败。
- 该 run 的 Recall@5/MRR/Top-1/Multi-hop 为 `0.881/0.8571/0.7143/0.7222`；main 基线 run `38397152826` 为 `0.9286/0.8571/0.7143/0.8333`。两边 Top-1 均低于门槛，没有改阈值或删案例。Recall/Multi-hop 差异集中在 RC-024；其 trace 为 `rewrite skipped`、0 variants，因此差异不能归因于 MultiQuery。复杂查询 RC-031 在 1.8 秒等待预算后 timeout 并回退原始 query。
- 实际账目 31 次 Provider 调用：embedding 22、LLM 1、rerank 8；其中 22 次身份归属不匹配 `tenant=eval/user=github-actions-rag-smoke`。31 次均为 `unpriced/price_unknown`，`cost_verification` 与 `budget_ledger_verification` 都是 `UNVERIFIED`；唯一预算预占为 `needs_review`，预占 ¥0.5、未结算。账本保留待核查状态，没有把未知成本按 ¥0 结清。
- 针对 22 次 embedding 未归属，`9f2574f` 已让所有 RAG 检索线程池自动复制提交方 ContextVar；本地真实线程测试通过，但本修复后的 head 尚未真实 Provider 验证。Provider 的实际价格仍需由可信配置提供，不能据此宣称费用对账完成。
- R0/R1 Embedding 去重、缓存实现未改；现有 R0/R1 性能/准确率证据保留。

### 主线 RAG Smoke 门禁诊断

- baseline run `38397152826`（main `2d1d0b1`）与第二次 #52 真实 Provider run `38481681899`（评测 SHA `d7496f5`）来自相同 `pr_smoke` 数据版本。两次均 8/8 case 通过而严格 gate 失败；RC-024 没有触发 MultiQuery 改写，不能把该差异归咎于改写。RC-031 timeout 后回退原 query。当前 head `9f2574f` 修复了检索线程身份传递，但没有新的付费验证。
- PR #52 免费 PR CI run `38484509377` 与 Secret Scan `38484509166` 在 head `9f2574f` 均 SUCCESS；本地两组合计 `69 passed`。PR #52 仍以旧 main `8887383` 为 base；PR #57 已把 main 推进到 `7633416`，PR #55 当前 OPEN 并基于该 main，按用户要求等待 #55 会话处理后再同步 P52。
- PR #56/B1-E 已按用户批准合并（merge `8887383`）。新 main 上复跑 Travel 后端定向集为 `78 passed, 5 deselected`，V2 前端为 `3 passed`，TypeScript 检查通过；无真实 Provider 调用。5 个 PostgreSQL 配置相关排除项此前已在干净基线复现。本机 Docker app/worker 的 PostgreSQL Checkpointer healthy，但 `ENVIRONMENT` 未设置，不能证明生产配置。

## 凭据处置

- 本轮按用户授权重新轮换本机开发 Redis 与 PostgreSQL 只读凭据；`.env` 更新后，Redis、PostgreSQL 新认证通过，相关本机应用/Worker/Redis 容器全部 healthy，`.env` DACL 保持仅当前用户、SYSTEM 和 Administrators。当前 `.env` 备份中的 PostgreSQL 候选旧口令认证被拒绝；Redis 当前旧值未从环境备份恢复作直接回放，不能声称完成该项直接测试。
- 安全 PR #51 移除只读数据库配置和迁移中的弱默认值，并修复迁移已记账后的凭据状态漂移及共享集群跨库角色误禁用风险。head `ddc1301` 的 Secret Scan 与 RAG PR Smoke 均 SUCCESS；`test_startup_validation.py` 合并前后均 55 passed；scratch PostgreSQL 18 双数据库/SCRAM 验证目标库角色处理及另一库角色隔离；tree/history/diff Secret Scan 退出码 0；GPT 复审无未解决意见。PR 已按用户批准合并为 `d3272c9`；合并后 GitHub combined status 未返回 status checks，定向回归通过且合并树与已验收 head 的 tree hash 一致。
- Secret Scan 标准 `--tree`、`--history`、`--diff origin/main` 均为 0 命中。可选 `--with-runtime-secrets` 报 59 条命中，全部映射到非秘密配置 `JWT_ISSUER` 和 `TOKEN_USAGE_LOG_PATH`，不是凭据命中。
- API Key 和生产/外部 Secret Manager 未触碰，按用户要求维持开发阶段以外的配置。PR #41 既有历史泄露未做共享历史重写；历史处置需单独评估影响，不宣称凭据 P0 整体关闭。

## 下一步

1. #52 暂不合并：head `9f2574f` 已提交推送、免费 CI 与 Secret Scan 通过。等待 PR #55 会话处理合并；之后将 P52 同步到最新 main 并重跑免费 CI。当前付费评测授权已用完，若要在同步后的新 head 验证质量与 ContextVar 账本，需要再次授权；保留 0.80 Top-1 门槛。
2. 凭据安全代码已合入，本机开发 Redis/PostgreSQL 只读凭据已轮换；API Key、生产配置和 #41 历史泄漏凭据未轮换/撤销，不宣称安全 P0 关闭。
3. B1-E 已合并；生产 Checkpointer/interrupt 运行配置仍未知，部署前需由脱敏生产配置或只读运行证据确认。
4. RAG R2/R3 和 Runtime 资源生命周期治理按用户要求暂不启动。

## 2026-10-15 C2 续工

- PR #52 当前分支：`codex/rag-multi-query-p0-20261013`，起始 HEAD `8eb3737e39b0e30eab3b6c104a7f5f53a3fa7aca`；从已核实当前 `origin/main` `bf3cddf7ac7329f576f036cb1a9717a8f96dfd27` 同步。PR #55 已合并，PR #52 原 base `888738365666706488492c90ee8b97d87b173d02` 已过期。
- 同步时只有 `backend/infra/llm/proxy.py` 冲突。合并保留 C2 的 role 级 fallback、取消/deadline、重试策略，并合入 main 的预算/quota 预占错误分类与 `provider_attempted` 语义；冲突已按代码两侧核对后消解。没有主动改旅游或管理端功能。
- 合并后免费定向回归 `141 passed`，覆盖 LLM budget/governance、MultiQuery、检索 deadline/thread pool、eval ContextVar/账本 artifact、token accounting 和 model-role API。
- 首次运行有 140 passed、1 failed：该 API 测试在未设 `AI_INTERNAL_TOKEN` 的本机环境按 fail-closed 返回预期 503；用仅测试进程内的合成 token 重跑完整集合为 `141 passed`，未改断言或安全行为。
- Python `py_compile` 与 `git diff --check` 通过。对包含历史代码的五个模块运行 Ruff 报 25 条既有风格问题（例如 import 排序/例外命名）；未用自动修复扩大范围。
- 本轮未调用真实 Provider、未消耗付费额度、未连接生产或共享数据库。Clash 下真实 API 不通；历史严格 Top-1 `0.7143 < 0.80` 与费用/预算 `UNVERIFIED` 保持为阻断，不降低 Gate。
- 下一步：提交并推送 main 同步及任务进度，更新现有 PR #52 base 到 main；检查免费 CI 和当前 head Review，处理新增的具体问题。真实 Provider 质量与账本复验仍须等待 Clash 可达及新的额度授权。

## 2026-10-15 合并后 main 对账

- 最新 main 为 `5df8b0603355566e7fa1eb87906129c11a8211ab`，已包含 PR #61 与 #70。C2 分支将该 main 以 merge commit `f2e47db7dd22a97015e1e782b29c3c425846858a` 合入，保留既有 C2 提交和 27 文件差异；PR #52 已恢复为 Open，base main、mergeable。
- 初次错误尝试 rebase 旧合并历史时在 `backend/infra/llm/proxy.py` 与本进度文件冲突；已中止 rebase、恢复原 HEAD `506cfacbd4739029a9a74f6b7eb6836d2f350673`，没有保留冲突文件修改。之后按原有 merge 策略无冲突合入 main。分支短暂被推到 main SHA 并导致 PR 自动关闭，随后用 expected-SHA 恢复原 C2 HEAD、合入 main 并重新打开 PR；当前远端头 `f2e47db7dd22a97015e1e782b29c3c425846858a`。
- 最新代码树 C2 免费定向回归 `141 passed`，命令覆盖 model-role API/config、budget/governance、MultiQuery、retrieval deadline/thread pool、eval context/账本 artifact 和 token accounting；仅测试进程使用合成 `AI_INTERNAL_TOKEN`。未发起真实 Provider 调用。
- Secret Scan run `38689068480` 已 success；RAG PR Smoke run `38689068456` 尚在运行。10 个历史 review thread 均已 resolved；本次只合入 main 新提交，没有修改 C2 源码。
- **状态仍 BLOCKED，不建议合并：** 最近严格评测 Top-1 `0.7143 < 0.80`；31 次 Provider 调用均未定价，其中 22 次 tenant/user 归属异常，费用/预算账本保持 `UNVERIFIED`。真实复验需要用户授权新增评测额度，并在实际 Provider runtime 验证 Clash 代理和账本对账；不降低 Gate。

## 2026-10-15 PR #52 遗留问题修复

- 在原 `codex/rag-multi-query-p0-20261013` 工作树续工，无未提交前置修改；合入最新 main `45b0a7b`。本轮源码改动限定 RAG、统一预算、评测 seed/门禁及对应测试，未自行修改旅游或管理端实现。
- 复查旧 run `38481681899`：Top-1 失败来自 RC-005（售后 FAQ 排在退货政策之前）及 RC-024（库存入库流程排在采购协作材料之前）；这两例均未触发 MultiQuery。当前为云端 rerank 补充候选标题/文件名上下文，不改排序分数、Golden、阈值或原文；质量改善仍需真实复验，不能据 payload 测试宣称 Top-1 已通过。
- embedding 缓存未命中和每次 rerank HTTP 尝试进入既有 `reserve_model_call/record_model_usage`；缓存命中不预占、预算拒绝不记虚假出站。超时/缺价保留 `needs_review`，预占作用域退出恢复外层 ContextVar。embedding 字符 token 估算改为 `usage_source=estimated`，不再冒充 provider exact。
- enforce 下评测预算绑定失败改为拒绝启动；CI 在任何付费 Probe/索引前检查权威价格与汇率，只有已批准价格快照才能导入隔离库。快照保留原始双人审查、来源、版本与有效期，重复导入不新增，同版本冲突拒绝并回滚；无新审批、无自拟单价。启用 MultiQuery 时显式绑定本次测试的聊天模型。
- CI 最终门禁要求费用可核算且身份/预占/结算完成对账；`UNVERIFIED` 不再只是 artifact 提示。一次 eval 请求显式设置调用数 64、token 32000、成本配置 CNY 0.50；64 次覆盖原先漏计的专项组件，不构成整项 CI/供应商账单绝对封顶承诺，Probe/索引仍须独立核对。
- **T0：PASS**，修改 Python 文件 `py_compile`、`git diff --check` 通过；新增测试文件 Ruff 通过。现有大文件的历史 Ruff 问题未扩大修复。
- **T1/T2：PASS**，定向集合 `182 passed in 79.58s`，覆盖预算/配额/计费、MultiQuery、retrieval deadline、embedding 缓存、rerank、ContextVar、seed 和 CI 门禁；全部为合成 Provider/内存账本，无真实模型调用。
- 新增专项预算、价格预检和 seed 回归已加入 PR 免费 CI；按工作流完整命令本机执行为 `50 passed in 6.40s`，只有既有 `TestCase` 模型名称产生的 collection warning。免费 CI 不读取付费 Secrets。
- **隔离 PG T2：PASS**，使用本机 PostgreSQL 18 二进制独立启动 `127.0.0.1:15452/pr52_isolated_test`，SCRAM 合成凭据；执行现行 031/066 迁移，实际导入合成价格快照两次仍 4 行，同版本改价被拒绝，价格预检通过；既有 PG 原子配额集 `1 passed in 3.76s`。集群已停止、临时密码文件已删除，数据证据在 `D:/tmp/pr52-pg-w_2zfufw`。进程退出时既有 Memory async engine 有 event-loop-closed 清理告警，测试及数据库检查退出码为 0；不宣称已解决该独立生命周期问题。
- **真实 T3：BLOCKED / UNVERIFIED**。用户已经授权使用 PR 所需真实模型，不再等待重复授权。`prompt-eval` 环境未提供 `PROMPT_EVAL_APPROVED_PRICES_JSON`；授权源库只读查询返回 `ConnectionTimeout`，未取得权威价格。未启动新付费 run、未写共享/生产库。源价格与凭据可读后，先导出原始批准快照，再对当前 PR HEAD 执行一次固定 8-case 严格 Smoke（MultiQuery 开启），核对完整归属和费用；Top-1 0.80 门槛保持。
