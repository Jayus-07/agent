# 智能电商客服作品集计划

适用项目：Jayus-07/agent　｜　目标：展示客服系统中的受控业务流程、可靠执行和人工协同。

## 目标与边界

- 解释并展示仓库已经实现的能力，不把目标架构描述成现状。
- 演示只使用隔离的模拟业务数据，不连接真实支付或退款网关。
- 区分客服申请登记、人工审核和资金退款；案件 `open` 不表示退款成功。
- 结果和指标注明代码提交、命令、模型及样本范围；没有执行的项目不填写通过率或效果数字。
- 作品集改造任务已于 2026-10-20 取消（T2–T4 不再进行），本章任务引用随之归档。

## 实现事实源

| 能力 | 代码与现行资料 |
| --- | --- |
| 客服图、Supervisor 与业务专家 | `backend/customer_service/graph_builder.py`、`backend/customer_service/supervisor.py`、`backend/customer_service/experts/`；职责边界见[客服域说明](../../domains/customer-service.md)。 |
| 确认、幂等和结果未知处理 | `backend/customer_service/confirmation*.py`、`backend/customer_service/reconciliation.py`、`backend/shared/idempotency.py`；运维入口和审计要求见[副作用幂等运维手册](../../operations/idempotency-runbook.md)，CLI 源码为 `backend/scripts/idempotency_ops.py`。 |
| 退款政策与模拟执行边界 | `backend/customer_service/policy/refund_policy.py`、`backend/customer_service/service/refund_service.py`、`backend/customer_service/response/composer.py`；演示数据边界见[演示沙盒说明](../demo-sandbox.md)。 |
| 人工协同 | `backend/customer_service/handoff/`、`backend/customer_service/agent_assist.py`、`frontend-cs/`。 |
| 验收与评测数据 | [客服验收场景](../acceptance-scenarios.md)、`backend/evaluation/datasets/cs/` 及其 validator。测试或数据集存在本身不构成评测结论。 |

## 作品集交付项

### T1　退款政策口径

展示退款候选列表和资格判断使用同一结构化政策来源；RAG 仅用于说明和证据，不负责业务放行。配置无效时写路径 fail-closed，资格结果应携带政策版本。事实源为 `backend/customer_service/policy/refund_policy.py` 及对应服务和测试。

复现命令使用仓库相对路径和当前 Python 环境：

```sh
python -m pytest backend/tests/customer_service/test_refund_policy_consistency.py -q
```

该命令的具体执行环境和结果记入当次交付记录。真实 PostgreSQL 验收需要显式配置隔离 `REFUND_POLICY_PG_TEST_DSN`；不使用共享或生产数据库代替。

### T2　端到端演示

制作 `docs/customer-service/demo-walkthrough.md` 和可复用、脱敏的演示截图或录屏，展示订单查询、退款提案与确认、幂等重放、结果待核实及人工接管。每一步都标明使用模拟数据和实际业务状态。

涉及结果未知的人工裁决时，复用 `backend/scripts/idempotency_ops.py` 与[副作用幂等运维手册](../../operations/idempotency-runbook.md)中的受控入口、operator 记录和审计流程。只在获准隔离环境演示；不得手工改写账本或跳过裁决依据。

### T3　设计说明与评测证据

作品集设计说明覆盖 Supervisor / Expert / Service 分层、LLM 与规则及 RAG 的职责、确认状态机、幂等与未知结果、人工接管、安全边界和未实现能力。公共模块职责以[客服域说明](../../domains/customer-service.md)为准，新材料链接事实源而不复制完整规范。

评测需在固定提交和隔离环境中实际执行适用的客服定向测试、评测数据和 `scripts/cs_p0_acceptance.py`，记录命令、提交、模型、样本范围、通过率和失败样例。真实 Provider 调用须具备有效授权预算与费用归因；未执行的评测不填入数字。

### T4　局限与生产化路径

根据当前代码区分已实现、模拟和未实现能力。退款申请只代表按当前模式登记的客服申请；生产化路径必须以业务网关权威状态、资源授权、审计和结果对账契约为基础。此作品集范围不接真实资金操作。

## 验收原则

- 文档中的代码路径和相对链接均指向仓库现有内容。
- 演示操作与[沙盒说明](../demo-sandbox.md)、[幂等运维手册](../../operations/idempotency-runbook.md)的安全边界一致。
- 评测结果可由记录的环境和命令解释、复现；跳过或未运行的工作不标记为通过。
