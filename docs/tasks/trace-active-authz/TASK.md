# TASK: trace-active-authz — /traces/active 资源级授权 + 「运行中」筛选解锁（P1）

## 背景（2026-10-30 页面评审）

- `GET /observability/traces/active`（`observability.py:450-454`）**无任何 Depends 守卫**，
  返回内存活跃 Trace（含 question 等内容）。
- 因此前端把「运行中」状态筛选项 disabled 并挂 BLOCKED 牌子
  （`TraceFilter.tsx:29`、`TracesPanel.tsx:150-157/426-431`），内部债务外溢到用户界面。

## 目标

1. 后端：`/traces/active` 补 `Depends(_require_trace_reader)` + 资源级授权过滤
   （复用 `trace_visible_to`，与其他 trace 端点同口径），越权不进结果集。
2. 前端：解锁「运行中」筛选——下拉选项恢复可用，移除 BLOCKED 徽标与
   「运行中记录暂不可安全查询」拦截面板，改为真实查询 `/traces/active`。
3. 同步更新守护测试：`TraceFilter.test.tsx:20-46`、`TracesPanel.test.tsx:187-198`。

## 约束

- 授权语义与现有 trace 端点一致；`RuntimeOverview.tsx:217-220` 的总览 BLOCKED 一并复核
  （数据源同一端点，授权补齐后应可解锁）。

## 状态

- 2026-10-30：建任务。P0-A 之后实施（同文件 `observability.py`，避免冲突）。
