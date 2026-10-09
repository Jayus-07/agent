# 旅游域

## 入口与当前链路

- 对话规划域图注册于 backend/travel/register.py，图构建在 backend/travel/graph_builder.py。
- 节点由 slot filler、Supervisor、附加查询、规划专家、Validator/Repair、局部重规划和 Reporter 组成；真实顺序以图构建器为准。
- HTTP 与 SSE 入口位于 backend/app/api/routes/travel.py。
- 旅游 V2 的 Trip、Revision、Template 与搜索 API 位于 backend/travel_v2/api/，由 backend/app/api/router.py 挂载。

## 状态与约束

行程版本服务负责 Active/Draft 生命周期、确认、放弃、恢复和差异读取。读写按经过验证的用户、租户和会话范围隔离。只读对话不得改写行程或 Checkpoint；写操作必须遵守版本冲突和失败返回契约。

外部搜索或交通 Provider 失败时通过现有失败策略表达错误或降级，不将空结果或失败伪装成成功。

## 维护与验证

- Provider 与凭据入口见 backend/providers/travel/、backend/providers/ 和配置模块；不得在文档中复制动态 Provider 或密钥清单。
- 业务安全与服务依赖索引见 [Domain Service Map](../architecture/domain-service-map.md)。
- 定向测试入口：backend/tests/travel/、backend/tests/travel_v2/ 和 backend/tests/api/ 中对应旅游用例。