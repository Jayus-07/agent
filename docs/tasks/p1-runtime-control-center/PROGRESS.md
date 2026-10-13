# P1-G Runtime Control Center 进度

> 2026-10-13 建档。**设计经过用户认可；代码和真实 API 尚未施工/验证。**

| 批次 | 状态 | 已有证据 | 下一步 |
| --- | --- | --- | --- |
| UI-1 | DESIGN APPROVED；详细字段/状态待冻结 | 九页面交互 HTML/截图已在当前对话展示，数据为 Mock；原型文件未纳入仓库 | 冻结信息架构、详情流程、DTO/状态和响应式要求 |
| API-0 | NOT STARTED | 用户要求先设计 UI，本批故意未审查后端 | 在 B1 安全交接后对账现有 Admin/Backend |
| FE-1 | NOT STARTED | 无真实前端 PR | frontend-admin 真实只读页面 |
| API-1 | NOT STARTED | 无 API/权限/DB 实测证据 | 仅在差距成立时补最小适配 |
| GOV-1 | BLOCKED BY REAL BACKEND CONTRACT | Prompt/预算/审批必须依赖现有后端已通过验收的写路径 | 逐能力解锁真实操作 |
| T3 | NOT VERIFIED | 未做真实浏览器/API/租户与费用对账 | 逐项执行 RC-01～RC-14 |

**已作出的范围决策**：
1. 运行总览、执行记录与 Trace、Agent/Workflow、Tool/Capability、模型成本、Prompt、权限租户、审批审计、服务健康告警，共九页面。
2. 优先复用现有 `frontend-admin`；先只读，再写操作；Mock 在真实环境必须移除或标识为演示。
3. API 是目标契约草案，非当前后端事实；不得为了页面重建公共 Runtime。
4. 本轮仅计划文档变更，不修改代码、不运行生产验收、不声称 B1-A～F 已收口。

下一状态更新必须附：Git SHA/PR、T0～T3 真实命令/证据、权限和业务终态对账、阻断项及可回滚方案。
