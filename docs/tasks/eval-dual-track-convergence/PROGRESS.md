# 进度跟踪：评测中心双轨收口与运行记录统一

## 任务状态
- 当前状态: `VERIFIED`
- 分支: `feat/eval-records-unify`
- 工作树: `D:\Program Files\workplace\agent-eval-records-unify`

## 步骤进度
- [x] 代码与架构现状排查（双轨数据源与前端现状确认）
- [x] 编制 TASK.md 与 PROGRESS.md
- [x] 后端实现：`backend/evaluation_v2/repository.py` 统一记录投影与 CLI 记录适配（`_normalize_ledger_row`, `list_runs_unified`, `get_run_unified`, `get_domain_overview`）
- [x] 后端实现：`backend/app/api/routes/evaluation_v2.py` 路由改造与 `/domain-overview` 端点，放宽 run_id 为字符串格式
- [x] 前端适配：`frontend-admin/src/api/evaluation.ts`、`DomainOverviewBar.tsx`（直连服务端总览端点 + 优雅降级）与 `RunsTab.tsx`（双轨去重混排）
- [x] 自动化测试编写与执行（`backend/tests/evaluation_v2/test_dual_track_runs_unification.py` 6/6 通过，既有 `test_run_listing.py` 通过，前端 RunsTab 13/13 vitest 通过，tsc --noEmit 0 错误）
- [ ] 工作树收尾审计与向用户报告提交
