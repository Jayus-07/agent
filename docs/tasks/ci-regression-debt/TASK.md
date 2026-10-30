# CI 全量回归暗债清偿

> 任务 ID：ci-regression-debt
> 创建：2026-10-30；最近交接：2026-10-31
> 状态：IN_PROGRESS（专项一/二已完成并 CI 实证；专项三进行中）

## 目标

把每周 Full Regression（`.github/workflows/full_regression.yml`，13 suite matrix，约 12545 用例）的失败清单收敛到 0：逐周下降直至绿。

## 背景

基线 v1（run 40948443665，2026-10-29）共 108 失败。**基线 v2（run 41120872440，2026-10-31 凌晨，部分）：61 失败（净减 47），api / tools-security-obs / sql-infra 三 suite 清零**（各 suite 明细见 [PROGRESS.md](PROGRESS.md) 的 v1→v2 对比表）。

原三专项状态：

1. ~~挂死专项~~：**已完成（结论修正）**——api/rag 无挂死用例，"挂死"是收集崩溃（前序已根修）+ 失败用例耗时的历史叠加。api 两次 dispatch 0 失败，rag 正常跑完。
2. ~~环境缺失~~：**已完成**——demo 库（#140）、只读角色配套（#141）、WORKFLOW_DB_PGDATABASE env（#145）。
3. ~~真失败批定性~~：api 59 例（#139）、orchestration 15 例（#144）、残余 5 例（#144）已完成。

## 剩余计划（供续工，按优先级序）

### 专项三 A：xdist 顺序敏感隔离（剩余失败的最大公约数，~27 例）

特征：单跑全绿、xdist（`-n 4 --dist loadfile`）下失败——共享状态跨 worker/跨用例污染。

- **orchestration 7 例**（最优先，基线 v1 起就红）：`test_router_understanding.py` 5 例 + `test_clarify_flow.py::test_router_weak_hit_short_circuits_to_clarify` 1 例 + `test_selection_decision_event_loop.py::test_api_end_to_end_task_reaches_success` 1 例。症状 `KeyError: 'cs_context'` / `assert 'plan' == 'customer_service'`——cs_prefilter 分支没走。钥匙：定位 router_node → understanding/cs_prefilter 链路的模块级共享状态（单例/缓存/类属性）。本地复现方法：`pytest backend/tests/orchestration -n 4 --dist loadfile`（worktree 内，`/d/tmp/ci-debt/orch_xdist*.txt` 有历史输出）。
- **misc inventory 10 例**：`test_notification.py` 的 TestPlan/TestPolicyMatching——策略匹配返回了不该返回的 NotificationPlan（共享 notification_policy 表状态）。automations 已用 tenant 后缀隔离（#145），inventory 同款方法（租户/用户常量带 `PYTEST_XDIST_WORKER` 后缀）大概率直接消化。
- **travel booking 6 例**：`test_booking_lifecycle.py::test_concurrency_identical_requests[10/50]`、`test_booking_d2_two_hop` 2 例、`test_booking_routing` 1 例——并发/幂等用例互踩，逐个单跑→xdist 对比定性。
- **customer_service 4 例**：幂等竞争、状态时序，同方法。

### 专项三 B：misc 真回归候选与环境（~15 例）

- `messaging/test_kafka_publisher.py` 2 例：`No module named 'kafka'`——CI 与本地均未装 kafka 包。二选一：requirements 补 kafka 依赖（含连带 lock），或测试加 `pytest.importorskip`（需确认该测试的价值口径，不许裸 skip 掩盖）。
- `skills/test_base_error_and_params.py` 3 例：错误文案格式变了（「缺少必填参数 url」→「缺少必填字段 url」等）——对齐新契约文案，或定性实现回退。
- `inventory/test_notification.py` 若隔离后仍失败：对照 `git log` 定性是策略匹配实现变化还是断言过期。
- `business_report/test_chart_artifact_mode` 1 例（503 vs 401/403/404）、`tool_runtime/test_governance` 1 例（UNAVAILABLE vs UNAUTHORIZED）、`test_tool_stats_alignment` 1 例（lock 缺中文名）、`test_tool_tracing` 1 例（NoneType headers）——逐个 git log 定性。
- `domain_runtime` 2 例（cs_reporter/cs_action_expert、direct/travel）——待定性。

### 专项三 C：travel / travelv2 / cs / prompts / evaluation / repo-tests 逐个定性（~20 例）

方法论（与 api 批相同，已验证有效）：
1. 单跑失败文件 → 单跑也失败 = 测试资产漂移或真回归 → 对照 git log 找引入提交 → 改断言/迁移新端点/修实现（各带回归测试）；
2. 单跑绿 = xdist 竞争 → 归隔离（A 类方法）；
3. 每修一批 dispatch `suites=<name>` 验证。

travel 13 例里 `test_provider_contracts.py` 2 例（envelope keys locked）与 `test_decision_audit.py` 4 例大概率是真失败（非并发），先修；services-travelv2 10 例待定性。

### 收尾

- api/rag/backend-root 在 v2 run 被取消（runner 并发，从未启动）——例行跑或 `dispatch`（无参数全量 / `-f suites=rag`）补取全景。
- 每周失败数曲线：v2 起 api / tools-security-obs / sql-infra 应稳定 0，作回归门禁观测点。

## 验收

- 每周 Full Regression step summary 失败数逐周下降至 0。
- ~~api/rag suite 能在 50min job 上限内跑完~~（已达成）。
- 台账与 PR 描述如实记录；容器级部署验证 UNVERIFIED 必须显式标注。

## 纪律

- 所有改动一律走 PR 合 main；提交用 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定。
- 不扩大 mock、不删断言、不 skip/xfail 掩盖失败；未实际执行的验证不得报通过。
- 新迁移必须双登记 `.github/scripts/rag_smoke_path_filter.py` + `scripts/init_db.py` 并跑守卫测试。
- 测试-only 修复无需 rebuild；改了 backend 代码要在主仓库 rebuild 并确认健康。

## 关联

- 前序：PR #126–#132（main 基线漂移修复 + full_regression workflow）、PR #136（台账）。
- 本任务已合 PR：#139（api 批）、#140（demo 库）、#141（只读角色）、#144（orchestration 批 + reporter 实现）、#145（worker 隔离 + env）、#146/#149（台账）。
- 本任务文档：[PROGRESS.md](PROGRESS.md)（含 v1→v2 对比表、实证数据、坑位记录）
