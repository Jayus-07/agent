# 客服域

## 入口与当前链路

- Domain Graph 在 backend/customer_service/register.py 注册，适配器为 backend/orchestration/graph/cs_graph_node.py。
- 子图由 backend/customer_service/graph_builder.py 构建，包含状态加载、待确认处理、Supervisor、业务专家和 Reporter。
- 会话状态流转、工单、确认和派单分别维护在 backend/customer_service/；管理端 API 集中于 backend/app/api/routes/ 中的客服路由模块。

## 状态与约束

执行前加载当前会话状态；确认、人工接管和业务状态迁移遵循现有状态机。写操作必须经过现有授权、用户确认/审批和幂等机制。身份及租户范围来自服务端验证后的上下文。

客服知识查询与数据库查询分别走现有 RAG 和 SQL 能力，不在客服图中复制这些服务实现。失败、转人工和部分结果按状态契约返回。

## 维护与验证

- 核心实现：backend/customer_service/ 与 backend/customer_service/experts/。
- 服务边界：backend/app/api/router.py。
- 定向测试入口：backend/tests/customer_service/，权限和 API 变化还需选择 backend/tests/api/ 中对应用例。
- 不复制客服知识库文档作为系统架构规范。