# PROGRESS: 管理端 V2 工作台改造

## 批次 0 + 批次 1（2026-10-28，未提交待用户确认）

### 改动清单（12 文件）

| 文件 | 要点 |
|---|---|
| components/layout/DetailDrawer.tsx（新） | 右侧详情抽屉：标题/动作区/可选 Tab（含 disabled 壳位 Tab）/Esc+背景关闭/滚动锁 |
| components/layout/StatusChip.tsx（新） | 六 tone 状态徽章，文字恒在（不仅靠颜色表意） |
| components/layout/StatMetricBar.tsx（新） | 紧凑指标条（V2 工作台顶部一行式指标） |
| components/layout/DetailDrawer.test.tsx（新） | 开合/Esc/背景/Tab 回调/壳位 Tab/徽章/指标条 5 用例 |
| app/mcp/page.tsx（重写，1271→1115 行） | 连接中心：WorkbenchShell 四 Tab（全部/MCP/HTTP API/凭据管理，?tab= 进 URL）+ StatMetricBar（5 指标）+ 连接卡片列表 + DetailDrawer 五 Tab（概览/已发现工具/配置/运行健康壳/版本历史壳）+ 凭据管理真实数据表 + 配置导出下载；**审核/授权/测试面板整体迁出** |
| app/tools/page.tsx（重写，866→1708 行） | Tool 治理：三 subTab（registry/quality/contract，?tab= 同步）+ 三来源统一目录（lock+动态合并）+ 指标条 + 动态工具 DetailDrawer（概览/Schema/执行策略/调试/历史 + AI 语义壳位 Tab）；**审核表单新增业务域 select（governance.domain，后端 64fa7a8 已支持授权拦截）**；授权开关/调用定义版本对比迁入 |
| app/mcp/page.test.tsx（新，7 用例） | 指标条/导出/卡片/治理面板移除断言/抽屉/壳位/凭据 Tab/新建编辑分路 |
| app/tools/page.test.tsx（6→10 用例） | 按三 subTab 重定位 + 合并目录/指标条/抽屉断言（未弱化） |
| api/mcp.ts | 仅追加：exportMcpConfiguration()、McpGovernance.domain?: string |
| components/layout/navConfig.tsx | /mcp→连接中心、/tools→Tool 治理（path 不变） |
| components/workbench/WorkbenchShell.tsx | 追加可选 actions prop（页头动作区），既有 3 工作台不受影响 |
| cost-governance/budgets/ToolConstraintsSection.tsx | 指针文案随审核流迁移更新 |

### 验证（本机）

- `npx tsc --noEmit`：0 错误（两次独立复核一致）。
- `npx vitest run`：**82 files / 569 tests 全绿**（基线 81/557；rbac/design-tokens
  main 基线本就绿）。主会话独立复跑同结果。
- 容器级部署验证 **UNVERIFIED**（未提交未合并；重建须按 AGENTS.md 用含
  最新改动代码、必要时错位端口）。

### 取舍记录

- 新建连接保留单表单（V2 验收允许：单表单或分步向导均可，功能完整优先）。
- HTTP 工具登记/编辑调用表单留在连接抽屉（登记属连接资产操作非审核治理）。
- 删除连接移入抽屉配置 Tab 危险区（设计标准：危险操作视觉区分）。
- registry 与 contract 共用搜索词，ds/failed 筛选留在 contract。

## 置灰壳位清单（data-stub="demo"，演示型功能，后端能力未建设）

| # | 位置 | 元素 | 原因 / 对应下一批 |
|---|---|---|---|
| 1 | /mcp 连接详情抽屉「运行健康」Tab | 自动退避重连策略开关 + 故障模拟四按钮（全 disabled） | 主动健康监测（下一批 #16） |
| 2 | /mcp 连接详情抽屉「版本历史」Tab | 配置版本时间线（仅展示真实当前发现版本锚点） | 治理配置发布/版本（下一批 #08） |
| 3 | /tools 工具详情抽屉「AI 语义」Tab | disabled Tab + 常驻提示 | AI 语义契约（增强批 #19） |

后续批次将新增壳位：/releases 治理配置发布档、/evaluations Discovery
评测区块、Trace 选择原因字段（后端跟上后转真实）。

## 下一步

1. 用户确认 → 提交推送（批次 0+1 一个 PR，或与后端 64fa7a8 同分支拆分）。
2. 批次 2 前置：后端「治理配置发布/回滚+导入」（tool-mcp-governance
   下一批第 2 项）→ /releases 第一档从壳转真实。
3. 批次 2/3 施工。
