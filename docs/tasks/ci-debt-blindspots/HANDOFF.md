# ci-debt-blindspots 下一会话接手实施计划

> 2026-11-02 交接快照；状态 PAUSED。仅交接分析，不恢复开发。原计划 TASK.md、证据台账 PROGRESS.md；本文件说明当前剩余工作。恢复前以最新 Git/GitHub 状态为准。

## 1. 目标、总计划与停止条件

原目标不变：209例盲区进全量matrix、首曝清偿、双守卫、三处文档、main全量全绿及安全清理。主体已由PR #261合入，但main最终回归失败，不能标COMPLETED。

| 阶段 | 当前进度 | 下一会话动作与验收 |
| --- | --- | --- |
| P0 scheduled核对 | 完成 | run41426703350，旧matrix 14/14成功，无需重复等待cron |
| P1 七目录209例收口 | 已合main；misc通过 | 保留既有实现，不重复做全仓审计 |
| P1 本地首曝清偿 | 20→0 | 权限夹具12例、Router6例、治理2例；209例本地及CI模拟均通过 |
| P1 main最终全量 | **未通过** | run41454704459为14/15job成功，仅RAG1例失败；先定位修复，再定向CI与main无参数全量15/15 |
| P1.5 双守卫 | 完成 | 已注入违例失败、还原通过；若不改守卫不重复注入 |
| P2 三处文档 | 主体合入；最终统计未提交 | 更新main最终统计、run号、快照，不能使用分支数冒充main数 |
| 收官文档 | 未完成 | 验收通过才改COMPLETED；准备Diff→明确提交确认→文档PR→明确合并确认 |
| P3 工作树清理 | 未完成 | 检查提交/分支/忽略资产及备份，合并后清理；不强制删除 |

完成条件：失败有根因与回归证据；main misc及无参数全量实际通过；TASK/PROGRESS/现行文档准确合入；清理或明确保留资产；逐项验收后才完成持续目标。

## 2. 工作树、分支和当前未提交内容

- 仓库：`D:/Program Files/workplace/agent`，本机main仍停70f6351，含其他会话资产，**不要在那里改代码/构建/擅自pull**。
- 本任务复用：`D:/Program Files/workplace/agent-wt-ci-blindspots`。
- 分支：`fix/ci-debt-blindspots`；当前HEAD：`5723d354b3529122a706ffa2db9783d9e0270f3c`（PR261 merge）。实现提交420f5ace967763c745d215b76412bd88cd3da40a。
- 未提交：`docs/operations/ci-evaluation-environment.md`、本目录`TASK.md`、`PROGRESS.md`；另有本次新建`HANDOFF.md`。内容是证据回填、验收勾选与暂停记录；不要reset/stash覆盖。
- 前序待清：`D:/Program Files/workplace/agent-wt-ci-debt2`，分支fix/ci-guard-t1-verify，HEAD ae292c6；此前核对已是main祖先，tracked/untracked干净，但有忽略资产，清理前重新核对。
- `.env`已在本任务工作树。临时文件用`d:/tmp/`，Python `D:/Python/python.exe`，PowerShell。
- 两树非缓存资产备份：`d:/tmp/ci-blindspots-preserved/`，含.env、日志、xdiag；已逐文件SHA256校验，**不要上传或输出密钥内容**。仍需检查备份后新增资产。

恢复起手式（不创建重复worktree/PR）：

```powershell
Set-Location 'D:/Program Files/workplace/agent-wt-ci-blindspots'
git status --short
git log -1 --oneline
git diff --stat
git fetch origin
git log -1 --oneline origin/main
```

先读最新AGENTS.md与本目录文档；对照上游差异，保留未提交文档。上游有冲突先报告，不能覆盖他人成果。

## 3. 最新失败：下一会话第一优先级

main无参数run [41454704459](https://github.com/Jayus-07/agent/actions/runs/41454704459)，HEAD5723d35，已completed/failure：12个suite+matrix+summary成功，仅pytest rag失败（合计14/15成功）。

唯一失败：

`backend/tests/rag/test_embedding_migration_worker.py::test_metered_cloud_protocol_uses_same_rebuild_and_price_ledger`

错误：`psycopg.errors.RaiseException: model_price is append-only (只允许关闭 effective_to，不允许改价或删行)`；触发器`reject_model_price_mutation()` line23。

RAG结果：**1 failed,1709 passed,2 skipped /158.95s**。尚未定位是fixture清理/价格写入协议/顺序依赖/产品缺陷；不能直接归因为间歇红，也不能停用触发器、放宽断言或直接重跑碰绿。

资产：`d:/tmp/ci-blindspots-main-full-41454704459/failures-rag/`，含pytest_out.txt、failures_rag.txt与xdiag。先读失败栈对应位置，沿最小调用链核对测试fixture、价格账本迁移/触发器及当前契约。这是新增事实，本会话只下载证据，未修代码、未重跑测试。

实施顺序：
1. 从已有失败栈明确SQL操作与调用方；必要时补诊断断言拿一轮证据。
2. 按单跑→xdist/loadfile→CI环境模拟→RAG定向CI确认；只能修根因，不扩大mock、不skip/xfail、不削弱append-only。
3. 若是产品价格/计费链路缺陷，读取llm-budget-billing.md与相关现行契约，按高风险补对应审查与T2；不顺手重构。
4. 修复Diff及验证完成后请求明确提交确认，限定路径提交/推送/建修复PR；不能直接往main提交。#261已合，不重开同内容PR。
5. 核对PR HEAD及门禁，明确合并确认后正常合main；RAG定向通过后无参数全量，15个job全绿。

## 4. 已验证证据与测试策略

| 范围 | 证据 |
| --- | --- |
| 209例四worker | 209passed/115.49s；d:/tmp/ci-blindspots-baseline-fixed.log |
| 209例去dotenv的CI模拟 | 209passed/49.37s；d:/tmp/ci-blindspots-ci-env.log；脚本ci-blindspots-ci-env.py；仅连接凭据/端口沿用本机 |
| 权限业务及拒绝边界 | 36passed；ci-blindspots-api.log |
| Router安全契约 | 25passed；ci-blindspots-router-fixed.log |
| Tool Governance | 12passed；ci-blindspots-gov-fixed.log；三工具实现声明已登记 |
| 工具契约 | gen_tool_contract_lock.py --check：IN_SYNC；执行需PYTHONPATH=worktree |
| 双守卫 | ci-blindspots-guards.log；两项注入退出1、还原通过；最终Redis pytest1passed |
| 分支misc | run41372835023：925passed |
| 分支全量 | run41375707630：15/15success；12664passed/142skipped/9xfailed；不能当main最终结果 |
| scheduled旧matrix | run41426703350/main144aea7：14/14success |
| main misc | run41454697496/main5723d35：925passed/23.44s；ci-blindspots-main-misc-41454697496/ |
| main全量 | run41454704459：RAG1failed，其余job成功；总计数待下载全部suite重新汇总 |

最小充分验证：文档只做T0 Diff/链接/证据校对；失败修复做定向T1，涉及账本/并发/隔离补必要T2。任务本身要求最终CI全量，除此之外不反复本地全仓回归。existing skipped/xfailed必须如实统计，不称全用例通过。

常用命令：

```powershell
$env:HTTPS_PROXY=''
gh run view 41454704459 --json status,conclusion,headSha,jobs
gh run download 41454704459 -n failures-rag --dir <新的资产目录>
$env:PGHOST='127.0.0.1'
D:/Python/python.exe -m pytest backend/tests/rag/test_embedding_migration_worker.py::test_metered_cloud_protocol_uses_same_rebuild_and_price_ledger -q --no-cov --timeout=150
# 确认本机隔离库配置后再运行；首次只单跑，后续按依赖范围加-n4/loadfile。
gh workflow run full_regression.yml --ref <修复分支> -f suites=rag
# 最终合并后：
gh workflow run full_regression.yml --ref main
```

HTTP EOF/TLS偶发时重查同一run，不能当任务终止；gh直连，push用`ssh://git@ssh.github.com:443/Jayus-07/agent.git`。

## 5. 本机部署与边界

- #261运行声明改动曾由本任务工作树重建；容器声明哈希与工作树一致、app HTTP200、迁移/72项表结构正常、后端服务healthy，详见PROGRESS；不是最新main全业务验收。
- **dev-svc.bat do_rebuild实际忽略target**，`devctl.bat rebuild backend /y`仍会进入前端构建。前两次在前端替换前中止，整体命令退出1不能报告PASS；后端子阶段与独立健康实证通过。后续若产品代码需重建，先核对脚本最新行为与其他会话协调，防止覆盖前端。不要重复错误命令或在本任务擅自扩修脚本。
- 不改其他旅游/客服/trace工作树，不碰共享业务库，不清理其他容器；保留既有产品域移交两项（progit PDF、govtest finally）；rag_smoke定向清单不扩。

## 6. 收官交付与验证清单

- [ ] 账本失败根因、修复、定向实际验证证据齐全；如果无法复现，保留不确定性并补诊断而非宣称解决。
- [ ] main最终全量15/15success，13suite真正执行；下载各suite结果统计passed/skipped/xfailed，记录head与run号。
- [ ] 三处现行文档与代码一致；TASK状态改COMPLETED前逐条验收，PROGRESS补32起坑位与曲线（本地20→0；CI misc0；main全量RAG1→0）。
- [ ] 收官文档Diff实际T0通过，取得提交确认；限定路径add/commit，走文档PR，取得合并确认，不把#261授权延伸为未来PR合并授权。
- [ ] 两工作树提交与忽略资产复核、备份校验；仅已合并且安全时移除，不force清理。若仍有在途成果则保留并写明原因。
- [ ] 最终交付列PR/HEAD/run/测试等级/Review证据与限制，未验证P0/P1风险如实报告。全部目标真正完成后再complete持续目标。

当前不需要重新实现matrix或守卫，不需要重复已通过的审计；下一会话聚焦RAG1例失败和收官。

## 2026-11-03 更新：§3 失败已定位并修复，待提交确认

根因与验证证据见 PROGRESS.md「2026-11-03」节：gw2 残留守卫测试 finally 前缀宽匹配软关了 gw3 计量测试的 fresh 价格行（xdist 跨 worker 共库竞态），致 272 行断言失败 + 277 行被 append-only 触发器拒绝。修复已落在 backend/tests/rag/test_migration_residue_guard.py（未提交），定向验证 3passed + 注入违例断言命中 + 还原通过。下一步：用户确认提交 → 修复 PR → 合并确认 → RAG 定向 CI → main 无参数全量 15/15 → 文档收官 → 安全清理。

## 2026-11-03 终态：任务收官

main 无参数全量 run 41497402474（HEAD c9927083）15/15 全绿，12688 passed / 142 skipped / 9 xfailed。两起 suite 竞态（rag 残留守卫跨 worker 误关价格行、travel bulkhead 共享池未按用例隔离）分别由 PR #264 / #265 修复并合并。TASK 状态已改 COMPLETED，三处文档统计已更新为 main 最终数据。剩余：文档收官 PR 合并 → 工作树安全清理。
