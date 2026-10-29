# PROGRESS: trace-error-diagnostics

- 2026-10-30：建任务。根因定位完成（error 信息在失败 span metrics/events，顶层 error 为空）。
  数据实证：PG trace_store 错误 trace `error={}`，失败 span `metrics.error_code=SIMULATED_TIMEOUT`、
  `metrics.error=模拟上游响应超时`。worktree `agent-wt-trace-errdiag`，分支 `feat/trace-error-diagnostics`。
- 2026-10-30：实现完成，未提交。
  - 后端：`_trace_dto.py` 新增 `_derive_trace_error`（span 聚合派生，存量键优先，消息截断 200 字），
    列表/详情两个 DTO 出口接入；`redaction.py` masked 级 PII 打码 error、summary 级剥离 message；
    `observability.py` 详情读取下发内容时写 `ADMIN_ACTION/trace_content_view` 审计（旁路软失败）。
  - 前端：`InputOutputPanel` 重写为「问题与结果」面板（问题/结果/错误码/类别/消息，空值如实显示）；
    详情页错误面板展示错误消息；CSV 导出增加 error_code 安全字段。
  - 测试证据：
    - 后端 `pytest backend/tests/observability/test_trace_error_derivation.py
      backend/tests/observability/test_phase3_privacy.py backend/tests/api/test_observability_trace_authz.py
      backend/tests/api/test_observability_trace_children.py` → 50 passed, 2 failed（children 两用例为
      main 基线预存失败，未改动主仓库同样失败，与本改动无关）。
    - 后端 `pytest backend/tests/api/test_observability_trace_paging.py
      backend/tests/api/test_observability_tool_filter.py backend/tests/test_observability_routes.py`
      → 11 passed。
    - 前端 `vitest run InputOutputPanel/TracesPanel/TraceFilter` → 15 passed；`tsc --noEmit` 通过。
  - 测试密闭处理：既有 authz 两个 200 详情用例补 `record_security_event` 打桩（外部 DB 依赖）。
- 下一步：等待用户确认后提交分支并走 PR 合并。

