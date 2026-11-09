# PROGRESS: 预算治理第二批

## 已完成（含证据）

### B2-P0 调研（2026-11-10）
- 迁移机制：`backend/sql/migrations/` 编号 SQL + schema_migrations 记录；共享库已被并行会话占用 143_memory_product_scope（未合入 main）→ 本批从 **144** 起编。
- budgets 路由：GET /admin/budgets/reconciliation 已有（sweep+summary+items+report）；写操作走 `run_governance_mutation` + `operator.actor`。
- 前端：`frontend-admin/src/app/cost-governance/budgets/page.tsx` 待对账 section（纯展示表）；API 层 `src/api/budgets.ts`。
- budget_events 是阈值告警表 → 审计落独立 action 表。

### B2-P1 存储层（2026-11-10）
- `144_budget_reservation_resolution.sql`：resolution/resolved_by/resolved_at/actual_cny 列 + CHECK + 开态部分索引 + `budget_reservation_resolutions` append-only 审计表。已应用隔离测试库验证（列/表均存在）。
- `quota.py`：`resolve_needs_review()`（幂等、锁序 user→tenant/day→month、settled_actual 记账/其余仅释放、审计落表）、`dry_run_backlog_migration()`（test_eval→local_unpriced→small_unknown→large_unknown 分类 + 建议动作 + 样例）、`list_pending_review(include_closed/include_test)`（resolution 字段输出）。
- 消费点过滤 resolution=''：summary.unsettled_reserved、reconciliation_summary.held/pending/by_reason/未决率窗口——closed 不再计入占额。
- 配置：`BUDGET_TEST_REQUEST_PREFIXES`（eval,synthetic,probe,recon_test）+ 既有 BUDGET_TEST_TENANT_PREFIX。

### B2-P2 路由（2026-11-10）
- POST /admin/budgets/reconciliation/resolve（单笔，幂等）
- POST /admin/budgets/reconciliation/resolve-batch（≤500 笔逐笔幂等，返回 resolved/already_resolved 计数）
- GET /admin/budgets/reconciliation/dry-run（只读分类报告）
- GET reconciliation 增加 include_closed/include_test 参数。
- 测试：`test_budget_quota.py` 9 passed（含核销幂等/释放/审计/closed 过滤/test 过滤/dry-run 分类，隔离库）。

### B2-P3 前端（2026-11-10）
- page.tsx 待对账 section 重做：明细默认折叠（首屏只看摘要+本月异常数）、展开后时间筛选（7d/本月/全部）+ 已核销/测试数据开关 + 勾选批量核销条（动作/原因/settled_actual 金额）+ 存量 dry-run 展示。
- budgets.ts：resolve/resolve-batch/dry-run API + item 类型扩展。
- `tsc --noEmit` 通过。

## 已完成（含证据）

### B2-P4 存量迁移（2026-11-10，用户确认全量三类后执行）
- dry-run 报告：121 笔 = local_unpriced 60 笔/¥30.00 + small_unknown 43 笔/¥39.70 + test_eval 18 笔/¥8.90，无大额未知。
- 用户确认后执行 resolve-batch 语义（store.resolve_needs_review 逐笔）：121 笔成功核销、0 幂等跳过。
- 结果核对：`unsettled_reserved` 78.599416 → **0.000000**；待对账队列 121 → **0 笔**；账本剩余占额 227.99 → **0.000000**；审计行 121（budget_reservation_resolutions，含 reason/resolved_by）。

### B2-P5 最终回归与部署（2026-11-10）
- 迁移 144 已随 db-migrate 应用共享库（fail-fast 补登 scripts/init_db.py MIGRATION_TARGETS 后 rc=0）。
- 最终定向回归 43 passed（budget_quota/estimated_settlement/local_inference/bypass_diagnostic/reconciliation_report/budget_reconciliation/soft_overflow）。
- `/health` 200、18 容器 healthy；前端 tsc 通过。
- 脚本留档：tmp/backlog_migration_execute.py（分类取 id + 逐笔核销 + 前后基线核对）。

## 完成状态

- 第二批全部完成：核销/归档闭环 + 存量 121 笔迁移 + 页面折叠/筛选/批量核销 + 测试数据隔离。
- 第一批 + 第二批均未提交（20 个文件，+1034/-77 + 4 个新文件），等待用户确认后分两个 commit 提交/PR。
