# 智能电商客服作品集改造计划

适用项目：Jayus-07/agent　｜　目标：说明并复现客服系统的受控业务流程、可靠执行和人工协同。

状态核对日期：2026-10-16。退款政策一致性代码已合入；作品集演示、设计说明和可复现评测结果仍是计划交付项。计划中的目标不代表功能或质量验收已经完成。

## 目标与边界

- 先解释并展示仓库已有能力，不把目标架构描述成已实现。
- 演示只使用隔离的模拟业务数据，不连接真实支付或退款网关。
- 区分客服申请登记、人工审核和实际资金退款；案件 `open` 不表示退款成功。
- 结果和指标记录准确的代码提交、命令、模型及样本范围；没有执行的项目标为未验证。

## 已实现能力的事实源

| 能力 | 代码与当前资料 |
| --- | --- |
| 客服图、Supervisor 与业务专家 | `backend/customer_service/graph_builder.py`、`backend/customer_service/supervisor.py`、`backend/customer_service/experts/`；职责边界见[客服域说明](../../domains/customer-service.md)。 |
| 确认、幂等和结果未知处理 | `backend/customer_service/confirmation*.py`、`backend/customer_service/reconciliation.py`、`backend/shared/idempotency/`。运维裁决入口仍需核实，见下文 T2。 |
| 模拟退款和回复边界 | `backend/customer_service/service/refund_service.py`、`backend/customer_service/response/composer.py`、[演示沙盒说明](../demo-sandbox.md)。 |
| 人工协同 | `backend/customer_service/handoff/`、`backend/customer_service/agent_assist.py`、`frontend-cs/`。 |
| 验收与评测数据 | [客服验收场景](../acceptance-scenarios.md)、`backend/evaluation/datasets/cs/` 及其 validator。数据集或测试文件存在不等于评测已运行。 |

## 交付状态

### T1　退款政策口径统一：已合入

PR #61 已合入，merge commit 为 `3a12dabc56c9cd8dfd80c401944f2924e36b3339`。`backend/customer_service/policy/refund_policy.py` 是资格判断和候选列表共用的结构化政策来源；RAG 不负责放行，非法政策按 fail-closed 处理，政策版本进入资格和提案数据。

本次在隔离工作树执行了：

```powershell
D:/Python/python.exe -m pytest backend/tests/customer_service/test_refund_policy_consistency.py -q
```

结果为 32 passed。真实 PostgreSQL 验收 `backend/tests/customer_service/test_refund_policy_real_pg.py` 本次未执行；它要求显式提供隔离数据库 `REFUND_POLICY_PG_TEST_DSN`。不要把上述单测结果表述为真实数据库验收。

### T2　端到端演示脚本与录制：待完成

目标材料：`docs/customer-service/demo-walkthrough.md`，以及可复用、脱敏后的演示截图或短录屏。步骤可以覆盖订单查询、退款提案与确认、幂等重放、结果待核实以及人工接管；每步都要标明模拟数据和实际状态。

当前[沙盒说明](../demo-sandbox.md)提供已有开关和边界，[验收场景](../acceptance-scenarios.md)提供场景清单。旧计划引用的 `scripts/idempotency_ops.py` 在仓库中不存在；仅找到 `backend/customer_service/reconciliation.py` 的服务函数，未找到仓库内调用入口。确认获授权且可审计的隔离运维入口之前，不演示或记录手工数据库改写，也不声称结果未知可以由读者自行裁决。

### T3　设计说明与评测证据：待完成

作品集设计说明应说明 Supervisor / Expert / Service 分层、LLM 与规则和 RAG 的职责、确认状态机、幂等与未知结果、人工接管、安全边界及未实现能力。公共模块职责继续以[客服域说明](../../domains/customer-service.md)为准，新增说明应链接事实源而不是复制完整规范。

评测结果需要在固定提交和隔离环境中实际运行 `backend/tests/customer_service/`、客服评测数据及适用的 `scripts/cs_p0_acceptance.py`，记录命令、提交、模型、样本范围、通过率和失败样例。若使用真实 Provider，先确认已授权的调用预算和费用归因；未运行的评测不填入数字。

### T4　局限与生产化路径：待完成

设计说明应从当前代码确认并区分已实现、模拟和未实现能力。当前[客服域说明](../../domains/customer-service.md)指出：退款申请只在 sandbox 模式下登记为等待人工审核案件；HTTP 业务网关没有退款 API，确认请求会 fail-closed，不会本地伪造成功。生产化建议应以业务网关的权威状态、审计和结果对账契约为前提，不接入真实资金操作作为作品集演示的一部分。

## 后续顺序

先完成隔离环境下可安全复现的 T3 评测，再制作不含未验证状态的 T2 演示材料和 T4 局限说明。若 T2 必须依赖人工裁决，先确认授权运维入口；否则保留该步骤为未完成，不绕过治理边界。
