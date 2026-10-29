# PROGRESS: trace-active-authz

- 2026-10-30：建任务。worktree `agent-wt-trace-active`，分支 `fix/trace-active-authz`。
- 2026-10-30：实现完成，未提交。
  - 后端：`/traces/active` 补 `Depends(_require_trace_reader)` 身份闸 + `trace_visible_to`
    资源级过滤（无归属记录仅 super_admin 可见，从严口径与其他端点一致）；
    `tracer.list_active` 补下发 `tags`（授权判定依据）与 `timestamp`（展示需要）。
  - 前端：TraceFilter「运行中」选项解锁为「运行中（实时）」；TracesPanel 运行中视图改走
    `listActiveTraces()` 授权端点（无分页/facets），移除 BLOCKED 拦截面板与「暂不可统计」；
    RuntimeOverview 的 BLOCKED 卡片改为授权活跃端点实时计数卡（可点击进入运行中筛选，
    失败时显示「未采集」不伪造计数）。
  - 测试证据：
    - 后端 `pytest backend/tests/api/test_observability_trace_authz.py backend/tests/observability/`
      → 177 passed, 1 skipped（新增 4 个活跃端点授权用例 + 1 个 tracer tags 下发用例）。
      注：首次运行 retention 1 例失败为 worktree 缺 `.env` 回退连错 PG（宿主 5432）的环境问题，
      按仓库纪律从主仓库拷贝 `.env` 后复现消失；非代码回归。
    - 前端 vitest（TraceFilter/TracesPanel/RuntimeOverview/MonitoringWorkbench）→ 17 passed；
      `tsc --noEmit` 通过。
  - 守护测试同步更新：TraceFilter 运行中 disabled 断言、TracesPanel BLOCKED 面板断言、
    RuntimeOverview「不生成运行中链接」断言均按新契约改写，并新增活跃端点失败降级用例。
- 下一步：等待用户确认后提交分支并走 PR 合并；合并后统一 rebuild 主栈做页面级验证。
