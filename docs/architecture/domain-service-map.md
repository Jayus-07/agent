# 域服务与 Provider 边界

本文提供稳定的源码定位和跨域约束，不维护动态 Tool、模型、Provider 或凭据清单。

## 服务定位

| 领域 | 主要实现入口 | 服务边界与约束 |
| --- | --- | --- |
| 客服 | backend/customer_service/、backend/customer_service/register.py | 客服状态机、专家、工单与人工接管；业务查询复用 RAG/SQL |
| 旅游 | backend/travel/、backend/travel_v2/ | 对话规划、行程版本以及独立 Trip/Revision/Template API |
| RAG | backend/rag/、backend/services/rag_server.py | 文档处理、索引、检索和证据门；本地/远程服务边界由配置决定 |
| SQL | backend/sql/ | 受授权范围限制的只读业务查询 |
| 选品 | backend/selection_funnel/、backend/selection/ | 选品流程与业务决策模块 |

域内具体规则和测试入口见 [业务域导航](../README.md#业务域)。

## 外部服务与凭据

- 通用 Provider 实现位于 backend/providers/ 和 backend/infra/；旅游 Provider 位于旅游域 Provider 模块。
- API 路由和业务适配器由对应模块调用；不要在文档中创建另一份服务注册表。
- 凭据只从受控配置、数据库治理或运行环境读取。文档与日志只记录凭据名称或逻辑来源，不得包含密钥值。
- 服务启用状态和可选 Provider 由当前配置决定；新增、删除或改名时只更新单一事实源。

## 失败与降级

- 每个调用方区分传输失败、权限拒绝、参数/契约失败、空业务结果和部分成功。
- RAG 证据不足按拒答/澄清契约处理；不得把未检索到证据表述成已验证事实。
- SQL 授权或校验拒绝是终态拒绝，不降级为宽范围 SQL。
- Tool、Provider 或队列故障按对应业务契约显式失败或降级；不伪造结果、不静默丢弃任务。
- 写操作通过现有 Governance、确认/审批和幂等链路。

## 下游熔断

`backend/infra/circuit_breaker.py` 维护 `CLOSED → OPEN → HALF_OPEN` 状态机；调用方捕获 `CircuitBreakerOpenError` 后走自身明确的降级契约。`CIRCUIT_BREAKER_SHARED_ENABLED` 开启时，Redis 用于多进程失败计数和 OPEN 状态广播；Redis 不可用时回退进程内状态，不能让熔断存储故障变成请求失败。配置、共享行为和并发语义以 `backend/config/redis.py` 及熔断测试为准。

## 常用定位

- 服务装配：backend/app/api/router.py
- Capability 与 Tool 治理：backend/orchestration/router/capabilities.yaml、backend/core/tool_governance/
- 模型网关：backend/infra/llm/
- 认证身份：backend/security/、backend/app/api/
- 业务域源码与测试：backend/<domain>/、backend/tests/<domain>/
