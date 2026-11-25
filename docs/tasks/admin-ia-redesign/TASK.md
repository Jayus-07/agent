# Task: 管理端信息架构（IA）与导航重构 (Phase 1 & Phase 2)

## 1. 目标与背景
管理端目前存在页面繁多、业务边界模糊、监控子页面被伪装成侧边栏平级入口、业务告警与系统告警混淆、部分散装页面体验割裂等问题。
本任务旨在对管理端进行信息架构、导航重塑与散装页面标准化：
- 清晰划定业务域（业务运营、AI能力中台、运行调度、质量与可观测、安全与系统治理）；
- 消除监控工作台等多入口伪切页（将总览、Trace、网关、Token 等聚合为完整工作台入口）；
- 区分业务告警（商品库存预警）与基础设施服务告警（服务健康与告警）；
- 保留全部历史兼容路由 activePaths 与角色权限门禁（Viewer/Editor/Admin/部门主管）；
- 对 `/alerts`（商品库存预警）与 `/reports`（报告中心）进行标准化重构，统一接入 `AssetPageShell`、`StatMetricBar`、`DetailDrawer` 抽屉流与统一状态规范（EmptyState / ErrorState）。

## 2. 交付与验收标准
- [x] 侧边栏导航精简，去除同一工作台重复列出的 tab 伪入口；
- [x] 语义清晰：库存告警重命名为“商品库存预警”并归入业务运营；
- [x] 角色门禁 (visibleNav) 与深层路由激活 (getActiveNavLabel) 严格维持兼容；
- [x] `/alerts` 与 `/alerts/[id]` 规范化重构（接入 AssetPageShell、StatMetricBar、DetailDrawer 抽屉处置流）；
- [x] `/reports` 与 `/reports/[id]` 规范化重构（接入 AssetPageShell、StatMetricBar、DetailDrawer Markdown 预览流）；
- [x] 前端定向单元测试 (`navConfig.test.ts`, `alerts/page.test.tsx`, `reports/page.test.tsx`, `layout/*.test.tsx`) 100% 通过（7 文件 32 用例）；
- [x] 前端静态检查 (`tsc --noEmit`) 0 报错通过。
