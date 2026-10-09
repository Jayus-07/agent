# 旅游域

## 入口与当前链路

- 对话规划域图注册于 backend/travel/register.py，图构建在 backend/travel/graph_builder.py。
- 节点由 slot filler、Supervisor、附加查询、规划专家、Validator/Repair、局部重规划和 Reporter 组成；真实顺序以图构建器为准。
- HTTP 与 SSE 入口位于 backend/app/api/routes/travel.py。
- 旅游 V2 的 Trip、Revision、Template 与搜索 API 位于 backend/travel_v2/api/，由 backend/app/api/router.py 挂载。
- V2 对话规划流为 `POST /api/travel/v2/plan/stream`：复用现有旅游域图和 SSE 事件；内部会话键使用 `travel-v2:` 命名空间，不读取或写入已退役的 V1 行程版本账本。结果由 V2 Trip API 创建行程或按 `expected_revision` 更新。
- V2 结构化编辑使用 `POST /api/travel/v2/trips/{trip_id}/edits`，每次提交一个类型化操作、`expected_revision`、`change_summary` 和 `Idempotency-Key`。成功时在既有 Trip 行锁与 CAS 路径中追加 Revision；不调用 LLM，也不建立独立版本账本。操作校验锁定/固定项、日程冲突和已核实营业时间。
- V2 地点候选查询使用 `POST /api/travel/v2/search/places`，查询只读；候选需要由用户明确选择后，才通过结构化编辑加入或替换地点。Provider 未核实的坐标、价格和营业时间不得升级成事实。
- V2 规划与 Trip 读写都要求服务端验证身份，并按用户和租户授权；不得用前端身份字段替代服务端上下文。

## 状态与约束

V1 行程版本服务的 Active/Draft 生命周期接口已退役。V2 正式行程由 Trip Revision 管理；更新使用版本 CAS，恢复使用目标 revision。读写按经过验证的用户和租户范围隔离。只读查询不得改写行程；失败或版本冲突不得伪装成成功。

外部搜索或交通 Provider 失败时通过现有失败策略表达错误或降级，不将空结果或失败伪装成成功。

## 维护与验证

- Provider 与凭据入口见 backend/providers/travel/、backend/providers/ 和配置模块；不得在文档中复制动态 Provider 或密钥清单。
- 业务安全与服务依赖索引见 [Domain Service Map](../architecture/domain-service-map.md)。
- 定向测试入口：backend/tests/travel/、backend/tests/travel_v2/ 和 backend/tests/api/ 中对应旅游用例。
