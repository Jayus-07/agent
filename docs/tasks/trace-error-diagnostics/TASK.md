# TASK: trace-error-diagnostics — Trace 错误诊断链路 + 详情页问题/结果展示

## 背景与根因（2026-10-30 页面评审 + 数据实证）

- 运行监控「执行记录」页错误 Trace 从列表到详情均无法回答"错在哪"：
  - 详情页错误面板显示「错误码未记录」「错误消息已隐藏」；输入输出被 `InputOutputPanel` 整体遮蔽。
- 数据实证（PG trace_store，只读查询）：错误 Trace 的顶层 `error = {}`、span `errors = []`，
  真正的错误信息在失败 span 的 `metrics.error_code / metrics.error_class / metrics.error`
  与 `events[].message` 中——**没有任何一层做聚合**，这是"错误码未采集"的根因。
- `question` / `answer_preview`（finish 时截取前 200 字）在库内数据完整，仅前端不展示。
- 用户补充需求（2026-10-30）：**详情页里面要有问题以及结果**。

## 目标

1. 后端在 DTO 层从失败 span 聚合派生规范化 `error`（code/class/message/node/retry_count），
   列表与详情共用；不改动存储结构、不改采集链。
2. 详情页展示：用户问题、运行结果（回答预览）、错误消息；列表错误码徽标自动点亮。
3. 详情读取写入安全审计事件（复用 `record_security_event`，旁路软失败）。
4. 脱敏矩阵覆盖派生 error（masked 级别 PII 打码、summary 级别剥离 message）。

## 范围

- `backend/app/api/routes/_trace_dto.py`：error 派生辅助 + 两个 DTO 出口接入。
- `backend/observability/redaction.py`：error 字段纳入 masked/summary 处理。
- `backend/app/api/routes/observability.py`：`get_trace` 详情读取审计事件（软失败）。
- `frontend-admin/src/components/observability/trace/InputOutputPanel.tsx`：展示问题/结果/错误。
- `frontend-admin/src/app/observability/traces/[id]/page.tsx`：错误面板展示错误消息。
- `frontend-admin/src/components/workbench/observability/TracesPanel.tsx`：CSV 导出增加 error_code 安全字段。
- 测试：后端 DTO 派生/脱敏/审计；前端 InputOutputPanel 与 TracesPanel 导出断言更新。

## 不做（明确排除）

- 不改 TraceStore 存储结构、不改 tracer 采集链（聚合只在读取/DTO 层）。
- 不做完整回答回放（存储只有前 200 字预览；全量回答留存属 data-lifecycle 议题）。
- 不动「执行类型 / HTTP status」两处 BLOCKED（依赖 Span DTO 新字段，属后续任务）。

## 验收标准

- 错误 Trace 详情可见：错误码、错误消息、错误类别、失败步骤；成功 Trace 不受影响。
- 详情页可见问题原文与结果预览；`TRACE_DETAIL_LEVEL=masked/summary` 时按矩阵脱敏。
- 列表错误行直接显示错误码徽标（现有 `t.error?.code` 徽标被派生数据点亮）。
- 后端定向 pytest 全绿；前端 vitest 相关用例全绿。
