# TASK：测试资产治理与回归提速

## 目标

基于**当前仓库真实代码**治理测试资产：减少无效测试与不必要回归耗时，
同时保证有效业务契约、安全约束及关键失败路径的覆盖不下降。

## 范围

- 后端 pytest（`backend/tests` + 根 `tests/`）、前端 Vitest、4 个 CI workflow。
- 不改生产业务行为、不做主图/Agent 架构重构。

## 阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 只读测试资产审计与分类清单 | ✅ 完成 |
| Phase 2 | 按模块分批清理（FIX/UPDATE/MERGE/DELETE） | ✅ 已完成可执行部分 |
| Phase 3 | 回归机制优化（分级执行、耗时治理） | ⏸ 待他会话收口 |
| Phase 4 | 分级验证（T0–T3） | 🔶 局部已执行 |

## 验收标准

- 每个删除/替换都有代码或契约证据。
- 不通过批量 skip/xfail、降低断言或跳过安全校验制造全绿。
- 不覆盖其他会话修改。
- 治理前后测试数量与耗时可对账。

## 依赖与边界

- 仓库存在**多个并行 Codex 会话**在施工（travel 路由重构、orchestration graph、rag pipeline）。
- 共享 PostgreSQL / Redis / 索引，禁止并发跑可能冲突的集成测试。

## 需求变更记录

- 2026-10-09：用户确认「先完成 Phase 1，确认清单后再实施 Phase 2～4」。
- 2026-10-09：用户确认可引入 `pytest-timeout`。
- 2026-10-10：`docs/reports/`（含本任务报告）被其他会话删除 199 个文件，
  本任务记录改存 `docs/tasks/test-asset-governance/`。
