# 客服演示沙盒

演示模式为没有真实业务订单的账号提供隔离的模拟业务数据，便于展示客服查询、售后提案和人工接管流程。

## 边界

- `CS_DEMO_MODE` 默认关闭；启用时只在业务服务的查询/数据归属边界把 user id 映射到 `CS_DEMO_CUSTOMER_ID`。
- 认证身份、角色与权限仍使用真实登录上下文；演示模式不能放宽租户隔离或授权。
- 演示订单由 `backend/sql/seeds/demo_sandbox.sql` 提供，统一使用 `DEMO-` 编号。响应应明确是模拟数据，不得呈现为真实订单或真实交易。
- 演示知识库位于本目录的 `demo-kb/`，内容均为模拟政策；不代表真实商家的承诺。
- `CS_DEMO_FAULTS` 只用于演示故障路径；不得在真实模式影响 Provider、队列或业务结果。

## 维护入口

- 开关及故障注入配置：`backend/config/customer_service.py`
- 身份映射：`backend/customer_service/service/demo_mode.py`
- 种子数据：`backend/sql/seeds/demo_sandbox.sql`
- 物流轨迹演示：客服服务层的 demo provider
- 回归：`backend/tests/customer_service/test_demo_sandbox.py`

新增演示能力时保持模拟数据与真实数据显式隔离，并核对开关关闭、越权查询和故障注入失效路径。
