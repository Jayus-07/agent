"""backend/mock/business_service — business-service HTTP mock（批次B 联调）。

用途：Java business-service 未就绪期间，提供与真实服务**同契约**的只读
订单查询端点，打通「客服 → business_client → 业务网关」全链路联调。

契约（backend/mock/business_service/contract.md，Java 侧落地时照此实现）：
  GET /business/orders?user_id={id}          订单列表
  GET /business/orders/{order_no}?user_id=   订单详情
  GET /health                                存活探针

鉴权：配置了 INTERNAL_API_TOKEN 时强制校验 X-Internal-Token（fail-closed）。
数据：确定性伪随机（hash(user_id) 播种），同一用户永远同一批订单，
     便于联调断言；无任何外部依赖。
"""
