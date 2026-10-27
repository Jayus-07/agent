# TASK: 管理端 V2 工作台改造（融合版原型落地）

> 状态：IN_PROGRESS（批次 0 + 批次 1 已完成待确认提交；批次 2/3 未开工）

## 目标

把现有管理端页面的信息架构与交互形态升级为融合版 V2 原型（蓝白体系、
指标条 + 页级 Tab + 抽屉详情 + 专家子视图），**功能与数据链路全部保留**，
不逐像素复刻。基准：交接包 `agent_tool_governance_merged_v2.html` 与
`02_融合版V2功能验收基准.md`；遵循 docs/development/frontend-design-standard.md。

## 范围与批次

- 批次 0（共享层）：DetailDrawer / StatusChip / StatMetricBar 组件 + 测试；
  主蓝 #4D6BFE 已是既有 accent Token，无需改 Token。
- 批次 1（A 档）：/mcp → 连接中心（连接一级资产 + 四 Tab + 卡片 + 五 Tab
  抽屉 + 凭据管理）；/tools → Tool 治理（三 subTab + 三来源统一目录 +
  动态工具五 Tab 抽屉 + 业务域字段接入审核表单）。审核/授权/测试面板
  从 /mcp 迁入 /tools。导航改名（/mcp 连接中心、/tools Tool 治理）。
- 批次 2（B 档，未开工）：/releases 双档（治理配置发布依赖后端发布
  指针表）、/budgets 四档、/consistency 双档+定位深链、/prompts 五档
  分类、/evaluations Discovery 评测壳位。
- 批次 3（C 档，未开工）：/ 入口卡、Trace 选择原因/脱敏字段透出。
- 明确不动：/settings/models（V2 保留现有实现）；/approvals 等不在范围。

## 验收

- tsc --noEmit 零错误；vitest 全绿且无新增失败（断言按新结构修正，不弱化）。
- 每处被裁剪的演示型功能以 `data-stub="demo"` 标记并列入清单（见 PROGRESS）。
- 容器级部署验证在合并授权后 devctl.bat rebuild（UNVERIFIED 前不得宣称上线）。
