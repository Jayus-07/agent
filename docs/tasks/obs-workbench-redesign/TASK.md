# TASK: obs-workbench-redesign — 运行控制中心按模拟页重构（不复用旧组件）

## 背景

2026-10-30 页面评审 + 四页交互模拟（observability-four-tabs-mock.html）经用户确认：
执行记录/详情页用模拟页组件重写，**不复用**旧 TracesPanel/TraceRow/TraceFilter 与详情页
StepTimeline/FlameGraph/GraphTopology 等区块组件；运行总览/网关安全/Token 用量下一 PR。
替换消失分析（哪些功能消失、是否可消失）见评审对话记录与本文件「范围」节。

前置：#133（错误诊断链路）、#134（/traces/active 授权）已合并——重构基于两者形态。

## 范围（本 PR：列表 + 详情）

### 新组件

- `TraceListPanel.tsx`：单行筛选栏（时间 4 档 + 自定义区间、状态、来源、搜索 400ms 防抖、
  更多筛选折叠（Agent/KB/模型/Tool，facets 驱动））、只看根 Trace、显示问题摘要、
  自动刷新 30s、对比选择浮条（≤4，跳既有 compare 页）、问题摘要列 + 类型/域徽标
  （runKind.ts 映射，DTO 补 workflow_kind 前的临时口径）、真实排序、服务端分页。
  不可消失机制全部重实现：URL 深链同步（含新增 root/showq/since/until 参数）、
  越页回退、迟到响应丢弃、刷新保留旧数据 + 过期标记、空态/失败态区分、has_tool chip。
- `runKind.ts`：运行类型/域展示文案唯一映射（禁止其他文件复制）。
- `SpanGantt.tsx`：Span 甘特时间线（start_time 实测偏移定位、层级缩进、失败标红、
  未埋点黑洞斜纹段（root uncovered_ms）、点击展开指标/Input/Output/事件/HTTP 拆分）。
- 详情页 `[id]/page.tsx`：四问布局（① 错在哪：问题结果 + 错误诊断卡 +
  「将本题加入候选评测集」→ `POST /v2/intake/from-trace`（评测中心已有 intake 审核）；② 慢在哪：
  SpanGantt；③ 答了什么：全部 llm_call 明细 + skipped_stages；④ 花了多少：概要侧卡 +
  SLA 语义修正（失败运行「未参与判定」））。

### 确认砍掉（不迁移）

列设置、收藏（localStorage）、前端 CSV 当前页导出（后续服务端全量导出替代）、
详情页旧区块：LangGraph 拓扑、火焰图、StepTimeline 列表、HTTP 独立区块、
MultiQuery/忠实度/META 独立卡（忠实度等数据在 Span 展开指标中可见）。

### 下一 PR（未做）

- RuntimeOverview 重写（10 状态卡、时间窗联动、最近运行表、契约缺口披露块、业务域分布——需后端 domain 聚合或前端样本口径标注）
- GatewayPanel 重写（汇总/趋势/原因分布/would_deny 接 Prometheus + 访问审计明细表接 ai.gateway_access_logs）
- TokensPanel 重写（今天/7/30/90 天窗、含 OCR 组件、5 卡、调用明细分页表、模型热加载 registry 口径）

## 验收

- vitest：TraceListPanel 6 用例、SpanGantt 5 用例、详情页 4 用例全绿；tsc --noEmit 通过
- 旧组件及其测试删除后无残留引用（grep 验证）
- 容器级验证待合并后 devctl rebuild（本分支未合并，主栈不动）
