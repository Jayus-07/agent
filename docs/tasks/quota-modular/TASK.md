# TASK: Tier 10 - backend/infra/llm/quota.py 模块化重构治理

## 1. 目标
对 2215 行超大单体文件 `backend/infra/llm/quota.py` 实施职责解耦重构，消除上帝模块债务，重构后入口门面行数削减 85% 以上（< 300 行），100% 保持既有接口契约、Pydantic/Dataclass 类型定义与测试 monkeypatch 兼容性。

## 2. 范围
- 目标单体：`backend/infra/llm/quota.py` (2215 行)
- 子包路径：`backend/infra/llm/quota_service/` 或 `backend/infra/llm/quota_infra/`
- 架构守卫：`backend/tests/infra/test_quota_modular_architecture.py`

## 3. 验收标准
- 门面行数 < 300 行；
- 所有顶层类（QuotaConfigurationError, QuotaExceeded, BudgetPolicy, ResolvedBudgetPolicies, BudgetPeriods, QuotaReservation, QuotaManager, PostgresQuotaReservation, PostgresQuotaStore）与公开函数 100% 导出；
- 架构守卫测试 100% 绿灯；
- 关联单元测试 100% 通过；
- 创建 PR 并通过 CI，完成 Squash Merge 与安全收尾。
