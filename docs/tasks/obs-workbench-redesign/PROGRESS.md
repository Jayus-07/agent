# PROGRESS: obs-workbench-redesign

状态：**DONE**（PR-1 #133 前置 + PR-1 列表/详情 + PR-2 三面板均已合并；容器级验证 UNVERIFIED，见末条）

- 2026-10-30：建任务。前置 #133/#134 已 merged；origin/main ref 因网络抖动暂不可达，
  分支内容通过本地 merge 两分支获得（内容等价，PR 前需重新 fetch 对齐确认）。
- 本 PR 完成项（未提交，等用户确认）：
  - 新增 TraceListPanel / runKind / SpanGantt；重写详情页四问布局 + 候选集入口
  - MonitoringWorkbench 换用 TraceListPanel；删除旧组件 18 个文件（含孤儿测试）
  - 验证：vitest 28/28（observability 全域）、tsc --noEmit 通过
- 2026-10-30（PR-1 合并后补记）：列表/详情 PR 已按流程走 PR 合并；
  「下一 PR」三项（RuntimeOverview / GatewayPanel / TokensPanel 重写）由 PR-2 完成。
- 2026-10-30（PR-2 合并后补记）：PR-2 已合并（#135，commit 4e8f2b7）：
  - OverviewPanel：时间窗联动、执行错误卡/成功率/P50-P95/运行中（`GET /traces/active` 实时）、
    业务域分布（最近 200 条授权样本聚合，口径已标注）、9 终态全可点击、最近运行表、
    最近错误区、失败全屏态、双时间戳、契约缺口披露块保留
  - GatewaySecurityPanel：四状态卡 + 拒绝趋势 + 原因分布/would_deny + 访问审计明细表
    （30s 轮询、默认仅异常、auth 白名单标注、双数据源 available=false 显式降级）
  - TokensUsagePanel：今天/7/30/90 天窗、含 OCR 组件筛选、5 卡（缓存/推理子口径）、
    每日趋势成本/Token 切换、调用明细分页表（Trace 跳转）、billing=local 零成本声明
  - 删除旧 RuntimeOverview / GatewayPanel / TokensPanel 及其测试；新增三面板 10 用例
  - 验证：observability 域 vitest 33/33、tsc --noEmit 通过
- 文档同步核查（2026-10-30）：现行文档（trace-model.md 200 条样本口径、frontend-guide 等）
  未引用被删除的旧组件，无需修改；本文件与 TASK.md「下一 PR」节为唯一过时处，已更新。
- UNVERIFIED / 待办：合并后 `devctl.bat rebuild` 容器级验证尚未执行（两 PR 合并均为
  文档台账登记时点前完成，主栈未重建验证新前端；需要时启动主栈 rebuild + 浏览器验收）。
