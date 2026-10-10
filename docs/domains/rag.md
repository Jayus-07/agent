# RAG 域

## 入口与当前链路

- 检索问答链位于 backend/rag/chain.py，Pipeline 与索引能力位于 backend/rag/。
- 独立服务入口为 backend/services/rag_server.py；客户端、远程模式和服务挂载分别核对 backend/rag/client.py 与 API 路由。
- 当前存储、解析器和检索策略以实现及配置为准，不在本文手工维护模型、集合或 Provider 数量。

## 约束与错误边界

检索结果作为生成依据；遵循当前 Evidence Gate、引用和拒答契约。索引不可用、证据不足、模型拒答与基础设施错误要按既有结果类型处理，不能将无证据回答伪装为有效检索结果。

Evidence Gate 的行为实现位于 `backend/rag/evidence_gate/`，RAG 链路由 `backend/rag/chain.py` 调用；修改拒答、引用或降级行为时，检查 `backend/tests/rag/` 中对应契约测试。

## 上传元数据与级联决策

上传元数据统一使用 `UnifiedMetadata` 和 `DecisionEnvelope`（`backend/rag/preprocessing/metadata_schema.py`）；分类词表由 taxonomy 模块维护，规则词典和 taxonomy 的一致性由测试守护。级联决策可能来自规则、相似度分类、LLM、fallback 或人工复核；各来源都归一到同一 envelope，并保留证据、版本、耗时和 LLM 调用数。阈值、灰度比例、外部模型开关及影子模式从 `backend/config/rag.py` 读取，不在文档维护静态阈值。改动前检查 `metadata_router.py`、索引 metadata stage、`backend/tests/rag/` 与 `backend/eval/metadata_baseline/`。

文档读写执行资源级授权和租户范围校验。管理端索引与发布操作遵循现有审批及状态管理。

## 维护与验证

- RAG 设计细节与检索契约：以本文、backend/rag/ 和 backend/services/rag_server.py 为准。
- 定向测试入口：backend/tests/rag/ 及 backend/tests/ 中对应的远程模式、上传和授权用例。
