# 智能电商客服作品集交接

核对日期：2026-10-16　｜　核对基线：`origin/main` `5b372540800f1b6689d371e08e79fff009200425`

## 目标与边界

作品集目标和待交付项见[改造计划](portfolio-plan.md)。演示系统只使用隔离的模拟业务数据；当前退款申请不会连接真实支付或退款网关。代码、配置、迁移和通过的对应测试是实现现状的事实源，本文仅记录本次交接时核对到的证据。

## 当前状态

| 项目 | 核对结果 |
| --- | --- |
| T1 退款政策口径 | 已由 PR #61 合入；merge commit `3a12dabc56c9cd8dfd80c401944f2924e36b3339`。候选列表和资格判断使用结构化政策。 |
| T1 定向单测 | `D:/Python/python.exe -m pytest backend/tests/customer_service/test_refund_policy_consistency.py -q`：32 passed（本次在隔离工作树执行）。 |
| T1 真实 PostgreSQL | 本次未运行。`test_refund_policy_real_pg.py` 只接受显式配置的隔离 `REFUND_POLICY_PG_TEST_DSN`；不得改用共享或生产数据库。 |
| T2 演示脚本与录制 | 尚未形成可复现的作品集演示材料。现有[沙盒说明](../demo-sandbox.md)和[验收场景](../acceptance-scenarios.md)描述了已有能力与边界。 |
| T3 设计说明与评测结果 | 尚无本计划要求的作品集设计说明和当前固定提交的评测结果；不得把数据集或测试存在写成质量评测已通过。 |
| T4 生产化边界 | 现行客服域说明已记录 HTTP 业务网关没有退款 API、退款申请仅代表等待人工审核；作品集材料应链接并保持一致。 |

仓库中未找到 `scripts/idempotency_ops.py`，也未找到 `reconciliation.py` 中裁决函数的仓库内调用入口。因此，在确认实际授权的运维入口、审计和隔离数据流程前，不要把“人工裁决”步骤写成读者可直接运行的演示命令。

## 现行资料入口

- [客服域职责与安全边界](../../domains/customer-service.md)
- [演示沙盒与模拟数据边界](../demo-sandbox.md)
- [客服验收场景](../acceptance-scenarios.md)
- [文档维护与交付标准](../../development/documentation-standard.md)

后续更新先核对这些资料与当前代码；作品集计划记录目标，不替代客服域规范、验收报告或长任务的 `docs/tasks/<task-id>/TASK.md` 与 `PROGRESS.md`。
