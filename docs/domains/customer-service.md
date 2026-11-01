# 客服域

## 入口与当前链路

- Domain Graph 在 backend/customer_service/register.py 注册，适配器为 backend/orchestration/graph/cs_graph_node.py。
- 子图由 backend/customer_service/graph_builder.py 构建，包含状态加载、待确认处理、Supervisor、业务专家和 Reporter。
- 会话状态流转、工单、确认和派单分别维护在 backend/customer_service/；管理端 API 集中于 backend/app/api/routes/ 中的客服路由模块。
- 用户端智能客服整页为 `/customer-service`（cs-workstation-v2 重建版，组件在 `frontend/src/cs2/`，P8 已删除旧 `components/cs/*` 抽屉树）：AI 会话流（SSE 全事件、确认卡、候选点选）、转人工 4 态横幅、人工接管后用户可继续输入（`POST /cs/conversations/my/{id}/messages`，Idempotency-Key 必填）、退款/工单折叠卡、商城三页 `/mall`、`/my-orders` 与资金闭环（Mock 账本，余额/流水/支付/回款）。
- 坐席工作台在 frontend-cs（:3300）`/cs/*` 七视图（P8 由 `/cs2/*` 切换，组件命名空间 `src/cs2/` 保留）：`/cs/workspace`（三栏工作台：双 Tab 认领、快捷回复、AI Copilot、接单开关）、`/cs/conversations`（会话管理：回放/重派/强制接管/关闭）、`/cs/tickets`（工单流转）、`/cs/refunds`（退款审核，supervisor：队列/意见必填/通过回款/驳回/终态锁/审计）、`/cs/qa`（质检：抽检/AI 预填/评分/申诉终审/日报）、`/cs/dashboard`（六卡看板+绩效，supervisor 全量）、`/cs/knowledge`（未解决聚合/话术/FAQ 草稿/检索）。
- 坐席工作台实时层：WS `useAgentSocket`（ticket 鉴权、seq 游标补发、降级轮询）；坐席状态四态（`cs_agents.status`，away/offline 不派单）与接单开关（`accepting`）即时生效；坐席间转接复用 offer 状态机（拒单/超时回退 waiting_human 队列）；SLA warning/breach 分级事件幂等。
- 侧栏订单复用 `AccountService` 与 `OrderService`；演示沙盒开启时仍由客服业务层映射到演示客户，页面明确标记演示数据。

## 领域职责与安全边界

- Supervisor 按当前请求状态分派知识查询、业务查询、动作提案、投诉或人工接管；专家通过共享 state 和显式结果协作，不互相直接调用。
- 权限与租户范围只取自服务端验证后的身份；订单、工单及会话数据必须按资源授权隔离。输出守卫和知识库证据规则按当前安全模块及测试执行。
- 退款、退货等写操作先形成提案并校验必填信息，再进入用户确认、授权执行和幂等保护；`pending`、`executed`、`failed`、`unknown` 等状态不可互相伪装。
- 退款写入默认关闭。`CS_BUSINESS_GATEWAY_MODE=sandbox` 与 `CS_REFUND_REQUEST_MODE=sandbox` 时，确认后登记到 `customer_service.cs_case`；HTTP 模式须同时显式设为 `http`，退款申请由 business-service 创建。两条路径都只登记待人工审核，不执行资金退款。
- sandbox 活跃申请由 PostgreSQL 按租户、用户和规范化订单 ID 跨会话唯一约束；HTTP 活跃申请由 business-service 按相同所有权维度裁决。HTTP 失败不回退 sandbox；写请求无自动重试，网络错误或 5xx 结果未知时确认进入 `verifying`。
- 订单列表、详情和物流轨迹经 business-service HTTP 契约读取；商品/时间指称先用真实用户订单匹配，歧义时追问，不由模型提供订单 ID。唯一物流查询会写入会话级订单上下文，后续已支持的条件退款计划可以承接该订单。退款提案确认时重查订单归属、状态、金额及政策版本。
- 退款申请状态查询按服务端认证的 user 和 tenant 限定。未提交提案的原因修改使用 Proposal Version CAS 使旧确认失效；已提交申请的原因修改先形成新提案、用户确认后再调用 business-service，服务端只允许审核中的申请被修改。
- business-service Mock 在内存模拟订单、物流和售后状态，服务重启会清空售后申请；它不代替生产数据库，也不执行资金退款。接口事实源见 [Mock 契约](../../backend/mock/business_service/contract.md)。
- 坐席工作台通过既有实时事件与补偿查询同步队列和会话；人工接管的认领、回复和关闭服从服务端状态机。
- 管理端客服档案独立于平台用户维护：`/sys/rbac/customer-service/team` 只列出租户内标记为客服部门的成员，角色、容量、启用和接单状态由专用更新接口维护。客服部门类型与主管配置由访问控制的部门管理维护；用户调离客服部门或账号停用时，后端会停用其客服档案并关闭接单状态。
- 可选演示沙盒仅映射业务查询所用的 customer id，不改变认证身份或权限判断；开关默认关闭。见[演示沙盒说明](../customer-service/demo-sandbox.md)。

模块职责的代码入口是 `backend/customer_service/`，图装配以 `graph_builder.py` 和注册模块为准；本文不复制完整节点或意图清单。

## 状态与约束

执行前加载当前会话状态；确认、人工接管和业务状态迁移遵循现有状态机。写操作必须经过现有授权、用户确认/审批和幂等机制。身份及租户范围来自服务端验证后的上下文。

客服知识查询与数据库查询分别走现有 RAG 和 SQL 能力，不在客服图中复制这些服务实现。失败、转人工和部分结果按状态契约返回。

## 维护与验证

- 核心实现：backend/customer_service/ 与 backend/customer_service/experts/。
- 服务边界：backend/app/api/router.py。
- 定向测试入口：backend/tests/customer_service/，权限和 API 变化还需选择 backend/tests/api/ 中对应用例。
- 不复制客服知识库文档作为系统架构规范。
- 评测集由 `backend/evaluation/datasets/cs/` 与其 validator 管理，类别和样本数以数据清单与校验器为准。
- 决策层路由（2026-11-02 cs-evaluation-upgrade P3 词表扩圈）：ACCOUNT 域细分规则、t_logistics/t_delivery_estimate 细分词表、自然表达 patterns（订单状态/DEMO 单号/售后自然动作/地址密码/投诉/人工坐席/余额/取消订单）已入 `vocab.py::_CS_DOMAIN_RULES` 与 `fine_router._RULE_INTENT_MAP`；任何词表改动必须经 cs-gate 三份 golden 回放（test_cs_eval_gate）与决策层回放门禁（test_cs_decision_replay，锁版 v2.2 口径）双重护航，零回归才可合并。
