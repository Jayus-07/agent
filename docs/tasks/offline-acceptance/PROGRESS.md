# PROGRESS: 平台离线验收收口（offline-acceptance）

- 状态：IN_PROGRESS
- Worktree：`D:/Program Files/workplace/agent-wt-offline-audit`
  （branch `audit/offline-acceptance-r1`，基于 origin/main=`6555433`）
- 交接基线：main=`6555433`（含 #355/#356）；主仓本地 main=`7b0bfc5`
  （behind 45，未动）；gaps 工作树禁动。

## 2026-11-07 接手核实（全部只读）

- 远程分支 `codex/travel-provider-e0-hash`=`6454801` 已推送；**PR 原不存在**
  （`gh pr list --state all --head` 返回空），证实前序会话 PR 创建未成功。
- travel 工作树 HEAD=`6454801`、与远程同步、工作区干净；本地复跑
  `backend/tests/evaluation/test_eval_preflight.py` **13 passed**（38.62s）。
- gaps 工作树：分支基于旧 main `7b0bfc5`，未提交改动 **114 个文件**
  （含 `.github/workflows/*`、`Dockerfile`），按红线禁动。
- `codex/offline-eval-matrix`、`codex/offline-paid-gate` 两分支经 `git cherry`
  证实补丁已等价合入 main（对应 `a8fe74d` / `8b2ba9a`），工作树干净——
  已完工遗留，可列为清理候选（待用户确认后再动）。
- `docs/tasks/eval-closure` 已 COMPLETE（E′/D1/F/D3/D4a + 浏览器收尾均合并），
  与本任务无关；在线采样默认关闭，长期启用前需单独核验预算账本。

## PR #357（travel-provider content_hash 锁定）——已合并 ✅

- 2026-11-07 创建：https://github.com/Jayus-07/agent/pull/357
  `fix(evaluation): lock travel provider dataset hash`，base main，
  head `codex/travel-provider-e0-hash`，单提交 `6454801`。
- 改动面：manifest 补 `content_hash=78b892835152b262`；preflight 新增 2 个守卫
  测试（manifest 必须锁 canonical 样本；漂移时 `verify_dataset_integrity`
  fail-fast）；datasets README 新增 hash 治理规则；evaluation-governance.md
  更正现状描述。
- 验证：本地复跑定向测试 13 passed（38.62s）；Codex 自动 Review 完成无发现
  （`has_any_findings: false`）；免费 CI 全绿——RAG Smoke preflight（3m37s）、
  RAG Smoke 8 案例（4s）、Evaluation V2 free contract tests（4m14s）、admin
  build、scan、Detect RAG changes 全部 pass；付费 real API job 按策略
  skipping（不计作已执行）。
- **2026-11-07 05:38 UTC squash 合并，main=`52443a6`**；审计 worktree 已
  ff 到该提交。注：本机 Docker 重建/健康验证按交接排序属第 5 步，此 PR
  不含运行时服务代码，部署侧状态记 UNVERIFIED（待第 5 步统一验收）。

## 2026-11-07 逐模块离线盘点结果（静态盘点 + 本地定向测试双口径）

### 静态盘点（origin/main=`52443a6`，Explore 只读代理，全程带 file:line 证据）

- **canonical 层健康**：9 个带 manifest 数据集（rag/sql/cs/e2e/travel/travel-provider/
  planner/sql_v2/travel_v2）content_hash 全部声明且实测匹配，case_count 全部一致。
  交接预期的「travel-provider 缺口」已由 PR #357 补齐。
- **真实缺口清单**：
  1. 顶层辅助集 10 个（planner_params / tool_selector / tool_routing /
     router_domain / context_budget_golden / rag_100_docs / rag_test_kb /
     rag_cases_20 / judge golden / memory_golden）无 manifest/hash 锁；其中
     tool_selector 评测脚本以文件路径绕过 loader 校验（run_tool_selector_eval.py:184）；
     tool_selector / tool_routing / router_domain 三者无守卫测试、不在任何 CI。
  2. suite 级：cs_q3_acceptance / cs_q3_action_pool 未声明 cases_hash（loader 校验 opt-in，loader.py:205-213）。
  3. rag 版本漂移：manifest=`6.0.0-desktop-candidate.4`，两个 suite 与 snapshot 仍 candidate.1。
  4. 双事实源：sql_v2/cases.jsonl 与 sql/golden_v2.jsonl 字节级相同，前者无代码消费者。
  5. 文档漂移：scripts/weekly_eval.py:25 自述 rag_test_kb「v1.3/54 条」，文件实为 v4.0/103 条。
  6. 孤儿资产：rag_cases_20.jsonl、travel/golden_v1.baseline.json 全仓零引用。
  7. INVALID 语义缺口：markdown 报告把 INVALID 折叠成 FAIL（report/markdown.py:451）；
     旧兼容入口 compute_metrics_gate / builder.compute_release_gate 无 INVALID 态（已声明废弃）。
- **免费 CI 有效性核实成立**：INVALID 不可能计入通过（release_gate.py:457-468 overall
  仅 PASS 为真、cli.py:147-149 退出码三态、validity.py:277-283 历史 VALID 可被推翻
  而持久化 INVALID 不降级）；rag_smoke 汇总 job 按事件组合严格校验，无「跳过即放行」路径。

### 动态验证（本地定向跑，worktree 无 .env）

- `pytest backend/tests/evaluation/`：**7 failed / 522 passed / 37 skipped**。
- 5 个失败 = 夹具过时（SimpleNamespace 缺 `actual`，见 PR #358）→ 已修。
- 2 个失败 = DB 环境阻塞（test_managed_task_controls::test_failed_cancellation_sql_guards_retry_budget_and_error_type、
  test_rag_eval_migration::test_production_authorized_kbs_exclude_unified_eval_kb）：
  默认连 `localhost:5432` 失败（项目库在 5433）；两测试不在任何 CI；
  待隔离 PG 批次验证，**未算通过**。

### 修复批 1：PR #358（gate validity 夹具 + 免费 CI 盲区）——已合并 ✅

- `test(evaluation): align release gate fixtures with actual contract`（`48ae5b6`）
- `ci(evaluation): cover managed gate validity tests in free contract job`（`e46edd3`）
- 验证：该文件 **33 passed**（修复前 5 failed）；CI 免费契约测试实跑通过
  （若新增测试 ID 无效 pytest 会因收集为空而失败）。
- 2026-11-07 squash 合并，main=`561bad7`。

### 修复批 2：PR #359（cs q3 suite hash 锁 + weekly_eval 文档口径）——已合并 ✅

- `fix(evaluation): add cases_hash locks to cs q3 suites`（`5853c28`）+
  `docs(scripts): correct rag_test_kb version note in weekly_eval`（`4017997`）
- cs_q3_acceptance / cs_q3_action_pool 补 `cases_hash=0e0ec5bde442b0c1`（实测复算
  canonical 一致）；loader 实测加载 11/38 条通过；`test_cs_eval_kb_contract.py
  + test_cs_runner_v2.py` **20 passed**。
- **rag suite dataset_version 漂移（candidate.1 vs manifest candidate.4）核实为
  测试钉住的冻结声明**（test_rag_eval_suites.py:48-51「suite 文件的
  dataset_version 是冻结声明，loader 实际版本以 manifest 为权威」），非缺陷，不改。
- weekly_eval.py 文档口径 v1.3/54 → v4.0/103（既有 E402 为存量，不顺手修）。
- 2026-11-07 squash 合并，main=`2b63030`。

### 修复批 3：PR #360（markdown INVALID 三态透传）——已合并 ✅

- `fix(evaluation): preserve INVALID verdict in markdown machine summary`（`09059a4`）
- markdown.py:451 verdict 改为逐字透传 `gate["verdict"]` 三态 + 补 `invalid_reason`
  （人类可读头部 ：55-57 一直正确；JSON 报告一直完整）；
  新增 `test_report_gate_verdict.py`：FAIL 透传 + INVALID 透传（3 条命中
  Provider 错误签名的确定性构造）。
- 验证：**2 passed**；`test_report_shanghai_timestamp.py`、
  `test_metric_label_single_source.py` 同跑通过（markdown 构建面无回归）。
- 2026-11-07 squash 合并，main=`907ae13`。

## 遗留缺口（下一步输入，均未算通过）

1. **DB 环境阻塞的 2 个测试**（test_managed_task_controls::test_failed_cancellation_sql_guards_retry_budget_and_error_type、
   test_rag_eval_migration::test_production_authorized_kbs_exclude_unified_eval_kb）：
   需隔离 PG 验证，不连共享库（5433 红线）。
2. **顶层辅助集 10 个无 hash 锁**（盘点缺口清单第 1 条）：是否补锁/补守卫测试待逐项裁决。
3. **tool_selector / tool_routing / router_domain 三个评测脚本无守卫测试、不在任何 CI**。
4. **孤儿资产**（rag_cases_20.jsonl、travel/golden_v1.baseline.json）与 **sql_v2 双事实源**：
   删除属数据删除动作，需用户确认后另批处理。
5. **旧兼容入口**（compute_metrics_gate / builder.compute_release_gate）无 INVALID 态：
   已声明废弃、禁新增调用方，仅记录。
