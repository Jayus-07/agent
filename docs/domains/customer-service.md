# 客服域

## 入口与当前链路

- Domain Graph 在 backend/customer_service/register.py 注册，适配器为 backend/orchestration/graph/cs_graph_node.py。
- 子图由 backend/customer_service/graph_builder.py 构建，包含状态加载、待确认处理、Supervisor、业务专家和 Reporter。
- 会话状态流转、工单、确认和派单分别维护在 backend/customer_service/；管理端 API 集中于 backend/app/api/routes/ 中的客服路由模块。
- 用户端智能客服独立页为 `/customer-service`，复用 `CSDrawer` 的会话、确认、转人工及工单逻辑；`GET /api/cs/customer-context` 使用可信当前身份读取账户和近期订单，不接受客户端指定用户。
- 侧栏订单复用 `AccountService` 与 `OrderService`；演示沙盒开启时仍由客服业务层映射到演示客户，页面明确标记演示数据。

## 领域职责与安全边界

- Supervisor 按当前请求状态分派知识查询、业务查询、动作提案、投诉或人工接管；专家通过共享 state 和显式结果协作，不互相直接调用。
- 权限与租户范围只取自服务端验证后的身份；订单、工单及会话数据必须按资源授权隔离。输出守卫和知识库证据规则按当前安全模块及测试执行。
- 退款、退货等写操作先形成提案并校验必填信息，再进入用户确认、授权执行和幂等保护；`pending`、`executed`、`failed`、`unknown` 等状态不可互相伪装。
- 坐席工作台通过既有实时事件与补偿查询同步队列和会话；人工接管的认领、回复和关闭服从服务端状态机。
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
