# 进展记录

更新时间：2026-10-13（Asia/Shanghai）

## 当前基线与工作树

- 最新 `origin/main`：`48f3d4980b549a31fa9b2d2db96fd41e631bd9b3`，已包含 #41、#45、#47。
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
| 凭据安全治理 | #51 | Draft，head `0a5f04c` | Detect RAG changes / Secret Scan SUCCESS；RAG Smoke Evaluation 待完成；本地启动测试 35 passed、扫描器测试 10 passed | 待 review | 未改生产凭据；历史不重写；Redis/API 当前开发配置按用户要求保持现状 |
| RAG MultiQuery P0 | 待创建 | 代码完成，准备提交 Draft PR | 相关定向 16 passed；前序相关集 226 passed + 4 warnings、Deadline/Executor 15 passed | 尚无 PR review | 真实 Provider 成功响应与复杂查询 live 召回未取得 |
| B1-C | #45 | 已合并，`8f65712` | run 163 SUCCESS；隔离 PostgreSQL 定向集 273 passed | 旧 thread 已解决 | 无；按用户要求以当前 4 类 Workflow 验收 |
| B1-D | #47 | 已合并，`c2feb44` | run 165 SUCCESS；定向集 135 passed + 隔离 PostgreSQL 6 passed | 2 条旧 thread 已解决 | 无 |
| B1-E | 新 PR | 尚未施工 | — | — | MultiQuery 合并后再启动；先核实生产 Checkpointer 运行配置 |

## MultiQuery P0 本批实施

- `multi_query` 改写通过 `bind_llm_for_role` 进入 LLMProxy；空角色依注册表继承 main，并使用原温度/token 上限。
- 改写线程提交时复制 ContextVar，确保 tenant/user/trace、限流身份和请求预算一致传播。
- Proxy 消费角色 timeout、重试和 skip 策略；默认失败不调用裸全局 LLM 或备用模型，按规则回退原始 query。
- 等待超时会取消尚未启动的线程池任务；已运行的 Provider 请求无法被线程取消，会按 Proxy 的用量/预算语义留痕。
- 失败日志只记录异常类型；R0/R1 Embedding 去重和缓存实现未改动。

### MultiQuery 验证证据

- 相关定向集：`226 passed, 4 warnings in 99.89s`；其中 4 个 warning 是 Windows Proactor 事件循环清理告警。
- Retrieval Deadline/Executor 相关集：`15 passed in 10.23s`。
- 新增取消排队任务后，MultiQuery Token Budget + Proxy Governance：`16 passed in 4.83s`。
- `py_compile`、`git diff --check` 已通过；提交前还要运行新增行 Secret Scan。
- 修复前的一次真实调用曾出现重试和全局备用请求；3 条 `llm_usage` 与预算账本/tenant/user/trace 对齐，潜在已计费预占保留在 `needs_review`。
- Proxy 修复后只发起一次隔离开发租户真实 Provider 调用；Provider 仍返回连接错误，改写回退原始 query。该调用有 1 条失败 `llm_usage`，预算账本与身份归因一致，可能已计费预占保留在 `needs_review`。未再次调用真实 Provider。
- 因 Provider 未返回成功响应，本批复杂查询真实召回验收未完成；代码测试与 R0/R1 历史准确率证据不能替代该项。

## 凭据处置

- 已按用户授权轮换本机 Docker PostgreSQL 的只读角色口令：新口令从主机及 `app` 容器认证成功；旧口令被 PostgreSQL 拒绝；角色无建库权限，业务库无 CREATE 权限。
- 读取根目录 `.env` 的 9 个本地应用/Worker 容器已重建并全部 healthy；`.env` DACL 收紧到当前用户、SYSTEM 和 Administrators。口令值未写入日志、PR 或报告。
- 安全 PR #51 移除数据库配置和迁移中的弱默认值；CI 的 Detect / Secret Scan 已通过，RAG Smoke Evaluation 尚待完成。
- Secret Scan 标准 `--tree`、`--history`、`--diff origin/main` 均为 0 命中。可选 `--with-runtime-secrets` 报 59 条命中，全部映射到非秘密配置 `JWT_ISSUER` 和 `TOKEN_USAGE_LOG_PATH`，不是凭据命中。
- Redis/API 当前值本轮未修改，按用户要求维持开发阶段现状；本地生产/外部 Secret Manager 均未触碰。PR #41 既有历史泄露未做共享历史重写；历史处置需单独评估影响，不宣称凭据 P0 整体关闭。

## 下一步

1. 等待 #51 RAG Smoke Evaluation 与 GPT review；满足门禁后提交合并清单，获得用户批准再合并。
2. 创建 MultiQuery 小型 Draft PR，复核 CI/GPT review；Provider 成功响应仍是验收阻断，未完成前不标 READY_TO_MERGE。
3. MultiQuery 获批合并后，以最新 main 建立 B1-E 独立分支；先取得生产 Checkpointer 实际启用证据，再实施 Interrupt/Resume。
