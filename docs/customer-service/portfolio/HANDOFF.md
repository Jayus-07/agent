# 智能电商客服作品集

本目录记录客服系统作品集的目标与稳定交付范围。作品集改造任务已于 2026-10-20 取消（T2–T4 不再进行）；已交付范围以本文与代码为准。

## 目标与边界

说明并复现客服系统的受控业务流程、可靠执行和人工协同。演示使用隔离的模拟业务数据；不连接真实支付或退款网关。客服案件登记、人工审核和资金退款是不同状态，不能互相表述为成功。

## 现行资料入口

- [客服域职责与安全边界](../../domains/customer-service.md)
- [演示沙盒与模拟数据边界](../demo-sandbox.md)
- [客服验收场景](../acceptance-scenarios.md)
- [副作用幂等运维手册](../../operations/idempotency-runbook.md)
- [文档维护与交付标准](../../development/documentation-standard.md)

作品集计划见[portfolio-plan.md](portfolio-plan.md)。实现现状以代码、配置、Schema、迁移及对应测试为准；本目录不维护任务状态流水账，也不复制客服域和运维手册的操作契约。
