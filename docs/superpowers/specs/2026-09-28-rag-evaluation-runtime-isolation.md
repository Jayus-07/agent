# RAG Evaluation Runtime 隔离设计

## 目标

把 RAG 的离线索引构建、在线只读检索和评估只读检索明确分成三个运行模式，解决评估 CLI 冷启动误触发 `data/docs` 全量同步的问题。此次改造保持 Router、LangGraph 节点、SSE、checkpoint 和生产聊天协议不变。

## 运行模式

`RAGPipeline(mode="index" | "runtime" | "evaluation")` 是唯一新增边界。

- `index`：允许扫描文档、解析、chunk、metadata LLM、embedding、向量/BM25 写入；现有离线导入和显式索引入口使用该模式。
- `runtime`：加载已有向量与 BM25 索引，只执行查询侧 embedding、向量检索、混合检索和 rerank；初始化不扫描文档、不调用增量同步、不重建 BM25。上传/删除等显式写操作仍由专用索引服务负责，不通过 runtime 初始化隐式触发。
- `evaluation`：只读加载已有向量与 BM25 索引，禁止文档扫描、同步、metadata LLM、向量写入和 BM25 重建；索引或 BM25 缺失/失配时直接报清晰错误。

旧的无参 `RAGPipeline()` 保留兼容行为，避免存量离线脚本立即改变语义；线上 singleton 和评估 runner 改为显式传入 `runtime`/`evaluation`。

## 评估快照

评估数据集增加固定 snapshot 配置，至少包含 `kb_id`、`fixture_set`、`version_id` 和 benchmark cases。评估 runner 构造检索过滤器时同时绑定这三个身份字段，不依赖当前 `data/docs` 目录内容。

新增 `python -m backend.evaluation.import_fixture baseline` 入口，复用现有 fixture catalog/indexer，不复制索引逻辑。导入结果必须打印文档数、chunk 数、collection/version；索引 chunk metadata 至少包含 `doc_id`、`kb_id`、`fixture_set`、`dataset="rag_eval"`、`version_id`。

## 防误用与验证

新增测试保证 evaluation 不调用 sync、不写 vector，runtime 不在初始化阶段写 vector，fixture metadata 完整。最终按 Router 回归、fixture 导入、真实 RAG smoke 的顺序验收，并在 STOP B 报告中记录真实 Recall@5、MRR、NDCG 和运行模式证据。
