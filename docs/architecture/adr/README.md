# Architecture Decision Records (ADR)

> 重要架构决策的记录。每篇 ADR 编号递增、状态清晰、可追溯。
> 命名格式：`NNNN-kebab-case-title.md`

## 索引

| # | 标题 | 状态 | 日期 |
|---|------|------|------|
| [0001](0001-merge-dual-registry.md) | 合并 Skill 双注册表 | Accepted | 2026-08-03 |

## ADR 模板

每篇 ADR 包含：
- **背景**：为什么需要决策
- **决策**：选择什么方案
- **理由**：为什么选这个方案
- **备选**：考虑过但放弃的方案
- **影响**：代码/性能/测试/文档
- **验证标准**：如何确认决策落地成功

## 状态说明

- `Proposed` — 提案中，未实施
- `Accepted` — 已批准，正在/已经实施
- `Deprecated` — 已废弃，由新 ADR 替代
- `Superseded by NNNN` — 被新决策取代