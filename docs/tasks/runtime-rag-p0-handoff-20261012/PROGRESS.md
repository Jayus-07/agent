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
| 凭据安全治理 | #51 | Open，head 013c340，代码已补齐 | Detect、Secret Scan、PR preflight、RAG Smoke contract 全 SUCCESS；真实 API job 按 PR 策略跳过；只读迁移定向集 19 passed；隔离 PostgreSQL 16 认证/会话集成通过；tree/diff/history Secret Scan 0 命中 | 修复 4 条未解决旧 thread，等待新 head 复审 | Redis/API Key 按用户要求未轮换；历史泄漏未撤销，故 P0 凭据风险未关闭 |
| RAG MultiQuery P0 | #52 | Open，head 0c3239b，修复已推送 | Detect、Secret Scan、PR preflight、RAG Smoke contract 全 SUCCESS；真实 API job 按 PR 策略跳过；相关定向集 46 passed；真实 run #38430900200 于旧 head befbad0 完成 8/8 用例 | 修复 2 条未解决 review thread，等待新 head 复审 | Live Top-1 为 0.7143，低于 0.80 门槛，与 main 同值；费用核验 UNVERIFIED；当前 head 未重复付费评测 |
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

- 当前代码 head 0c3239b 定向回归：infra/test_b1b_llm_governance.py、test_multi_query_token_budget.py、test_retrieval_executor_isolation.py、test_multi_query_trace.py，46 passed in 22.38s；py_compile 与 git diff --check 通过。
- 本地取消与预算测试覆盖预占期间取消、同步/异步 Provider 阻断、未执行预占释放；真实 ThreadPoolExecutor 测试验证慢改写不能阻塞 outer 检索。
- 真实 Provider run #38430900200（https://github.com/Jayus-07/agent/actions/runs/38430900200）在 head befbad0 开启 multiquery=true，8/8 案例成功执行；实际统计 107 次 Provider 请求（chat 35、embedding 63、rerank 9）。
- 该 run 汇总 Recall@5 0.9286、MRR 0.8571、Top-1 0.7143、Multi-hop 0.8333。同 main 基线 run #38397152826 指标一致，未见召回退化；两个 run 均因 Top-1 低于 0.80 门槛失败，不能降低门槛掩盖。
- Provider 实际调用有证据；价格/费用核验为 UNVERIFIED。上述 live run 早于当前取消与线程隔离修复提交；为避免额外付费调用，没有在 head 0c3239b 再跑真实评测。新代码由定向测试和 PR CI 验证。
- R0/R1 Embedding 去重、缓存实现未改；现有 R0/R1 性能/准确率证据保留。

## 凭据处置

- 已按用户授权轮换本机 Docker PostgreSQL 的只读角色口令：新口令从主机及 `app` 容器认证成功；旧口令被 PostgreSQL 拒绝；角色无建库权限，业务库无 CREATE 权限。
- 读取根目录 `.env` 的 9 个本地应用/Worker 容器已重建并全部 healthy；`.env` DACL 收紧到当前用户、SYSTEM 和 Administrators。口令值未写入日志、PR 或报告。
- 安全 PR #51 移除数据库配置和迁移中的弱默认值；本批新增安全迁移 fail-closed 修复。当前 head 的 Detect、Secret Scan、PR preflight、RAG Smoke contract 全 SUCCESS，真实 API job 按 PR 策略跳过。新增行扫描、当前树扫描、提交区间扫描均 0 命中；scratch PostgreSQL 16 验证旧会话终止、旧凭据拒绝、新配置凭据认证成功。
- Secret Scan 标准 `--tree`、`--history`、`--diff origin/main` 均为 0 命中。可选 `--with-runtime-secrets` 报 59 条命中，全部映射到非秘密配置 `JWT_ISSUER` 和 `TOKEN_USAGE_LOG_PATH`，不是凭据命中。
- Redis/API 当前值本轮未修改，按用户要求维持开发阶段现状；本地生产/外部 Secret Manager 均未触碰。PR #41 既有历史泄露未做共享历史重写；历史处置需单独评估影响，不宣称凭据 P0 整体关闭。

## 下一步

1. CI 已通过，等待 #51/#52 当前 head 的 GPT 复审；所有合并仍受主线 RAG Smoke Top-1 门禁阻断，修复基线前不合并。
2. 不降低 0.80 Top-1 门槛；用同基线证据定位 main 的失败案例与指标差异。
3. B1-E 继续等待生产 Checkpointer 的真实部署证据；Redis/API Key 与历史泄漏凭据继续保持未轮换状态，不宣称安全 P0 关闭。
