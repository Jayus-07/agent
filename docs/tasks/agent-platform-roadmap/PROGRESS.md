# Agent Platform 总计划进度

> 更新：2026-10-15；以 main `5df8b060`、已合并 PR、开放 PR、当前源码和实际验证为准。此表不是部署或生产证明。

| 阶段 | 当前状态 | 下一项可确认交付 |
|---|---|---|
| P0 | 既有协作/架构/测试规范在 main；不重开全仓规范审计 | 按任务需要维护现行规范 |
| P1 | Runtime 多批次按各自 PR/安全证据跟踪；P1-G 已有 #54 基线；#68 安全升级、#63 健康/Agent 修复、#65 Trace 分页/统计均在 PR 中 | 建议按 #53 → #68 → #63 → #65 推进；#63 的真实权限验收受隔离环境阻断，未通过前不合并 |
| P2 | Conversation-first ADR/实现及真实验收仍需按模块核对 | 按 P2-0～P2-3 交付 |
| P3 | RAG/SQL/Memory 有代码基础，剩余门禁按本域计划跟踪 | 与主助手完成授权的真实调用闭环 |
| P4 | Travel V2 分批建设中 | 以真实 Trip/Provider/版本流程验收 |
| P5 | CS/选品已有基础，生产安全与商业试点独立验收 | AI 售后场景和客户验证 |
| P6 | 账本、Trace、评测、看板等横向能力部分已存在 | 继续供业务与 P1-G 复用，不建平行数据源 |
| P7 | 全平台集成和发布验收未完成 | 按部署/演示目标收集 T3 与安全证据 |

## P1-G 状态

- **#54 已合并**：六组侧栏导航、运行总览与执行记录/Trace 基线已进入 main。
- **#53 Ready for Review**：四份计划文档已对账 main `5df8b060` 和 #54/#63/#65/#68；当前仍为候选计划，待文档门禁后按顺序处理。
- **#68 开放**：frontend-admin 依赖升级到 Next.js 15.5.28；锁文件修复 head `85edd1b` 的 `npm ci`、Vitest、tsc、build、High/Critical audit、secret-scan、RAG PR Smoke 均 PASS，尚待合并。
- **#63 开放**：Agent/Workflow 与服务健康页 PR；Review 修复已推送，两条线程已解决，定向验证已完成。真实 Admin/Viewer/Editor、跨租户和资源权限 E2E BLOCKED；待安全 PR 合并后重跑门禁。
- **#65 开放**：交付 A 的 Trace 服务端分页、时间/状态筛选、授权统计和现有 UI 接线；统计受授权范围及 TraceStore 留存窗口约束，继续兼容最多 200 条样本展示；定向 API/UI 测试、tsc、build PASS，真实端到端 BLOCKED。
- 现有 Tool/Capability、模型/成本、Prompt、权限/租户、审批/审计页面或 API 在 main 中已存在；后续按差距整合，不重复建设。
- 对外开放仍被真实租户/资源权限验收阻断；main 的 high/critical 依赖修复待 #68 通过全部 CI 并合并。未认证 401 不等于已完成 API 授权验收。

P1-G 的权威计划和 RC-01～RC-14 进度见[管理端总计划](../p1-runtime-control-center/TASK.md)与[详细进度](../p1-runtime-control-center/PROGRESS.md)。

**进度记录要求**：区分 DESIGN、IN PR、MERGED、T0–T3 PASS、BLOCKED、UNVERIFIED。只合并计划文档不表示功能已上线；没有真实权限/数据证据不得标验收通过。
