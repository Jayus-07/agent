# Agent Platform 总计划进度

> 更新：2026-10-14；以最新 main、已合并 PR、开放 PR、当前源码和实际验证为准。此表不是部署或生产证明。

| 阶段 | 当前状态 | 下一项可确认交付 |
|---|---|---|
| P0 | 既有协作/架构/测试规范在 main；不重开全仓规范审计 | 按任务需要维护现行规范 |
| P1 | Runtime 多批次按各自 PR/安全证据跟踪；P1-G 已有 #54 合并基线，#63 开放修复中，#65 开放交付 A 的 Trace 分页/统计 | 先完成安全依赖门禁和 #63 真实权限补验；A 可并行推进，随后按 B/C 顺序交付 |
| P2 | Conversation-first ADR/实现及真实验收仍需按模块核对 | 按 P2-0～P2-3 交付 |
| P3 | RAG/SQL/Memory 有代码基础，剩余门禁按本域计划跟踪 | 与主助手完成授权的真实调用闭环 |
| P4 | Travel V2 分批建设中 | 以真实 Trip/Provider/版本流程验收 |
| P5 | CS/选品已有基础，生产安全与商业试点独立验收 | AI 售后场景和客户验证 |
| P6 | 账本、Trace、评测、看板等横向能力部分已存在 | 继续供业务与 P1-G 复用，不建平行数据源 |
| P7 | 全平台集成和发布验收未完成 | 按部署/演示目标收集 T3 与安全证据 |

## P1-G 状态

- **#54 已合并**：六组侧栏导航、运行总览与执行记录/Trace 基线已进入 main。
- **#53 Ready for Review**：四份计划文档已对账最新 main 与 #54/#63；PR 可合并、无冲突，尚未合并，合并前仍是候选计划。
- **#63 开放**：Agent/Workflow 与服务健康页 PR；Review 修复已推送为 62a8fad，两条线程已解决。真实登录态 E2E BLOCKED，依赖漏洞门禁仍失败。
- **#65 开放**：交付 A 的 Trace 服务端分页、时间/状态筛选、授权统计和现有 UI 接线；定向 API/UI 测试、tsc、build PASS，真实端到端 BLOCKED。
- 现有 Tool/Capability、模型/成本、Prompt、权限/租户、审批/审计页面或 API 在 main 中已存在；后续按差距整合，不重复建设。
- 对外开放仍被真实租户/资源权限验收和 high/critical 依赖漏洞门禁阻断；PR #63 本机只读接口未认证返回 401，不能当作 API E2E 通过。

P1-G 的权威计划和 RC-01～RC-14 进度见[管理端总计划](../p1-runtime-control-center/TASK.md)与[详细进度](../p1-runtime-control-center/PROGRESS.md)。

**进度记录要求**：区分 DESIGN、IN PR、MERGED、T0–T3 PASS、BLOCKED、UNVERIFIED。只合并计划文档不表示功能已上线；没有真实权限/数据证据不得标验收通过。
