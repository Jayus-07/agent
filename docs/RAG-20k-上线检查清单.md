# RAG 20k 上线检查清单

> 2026-09-19 合并 rag-eval-kb-unification：目标/入库/检索/报告/放行各节来自评测库统一工作；参数/验证/观察/指标口径各节沿用 2026-09-18 参数优化版。

## 目标与边界

- [ ] 20k 文档只在独立 staging 验证，不直接污染生产 KB。
- [ ] 评测写入 `kb_id=rag_eval_kb`，运行范围为 `fixture_set=scale_20k`。
- [ ] `rag_test_kb`、`rag_100_docs` 只读兼容，不再有新写入。
- [ ] 测试 KB 不出现在 customer/employee 的授权集合中。

## 当前默认参数

| 配置 | 默认值 | 作用 | 调参方向 |
|---|---:|---|---|
| `RAG_DOC_CANDIDATE_K` | 50 | Stage 1 文档候选池 | 召回不足调到 100/200；延迟高则回退 |
| `BM25_SEARCH_K` | 10 | BM25 兼容基础值 | 作为最终稀疏检索规模参考 |
| `BM25_CANDIDATE_K` | 100 | BM25 全局候选池 | 过滤后命中不足调大；内存高则评估 |
| `VECTOR_HNSW_EF_SEARCH` | 80 | pgvector 查询搜索宽度 | 过滤召回不足调大；CPU/P95 上升则调小 |
| `VECTOR_PG_POOL_MIN` | 1 | PGVector 连接池最小连接数 | 按并发预热需求调整 |
| `VECTOR_PG_POOL_MAX` | 10 | PGVector 连接池最大连接数 | 不得超过 PostgreSQL 连接预算 |

## 入库与一致性

- [ ] 用 `python backend/scripts/ingest_eval_fixtures.py --fixture-set baseline` 验证小集幂等。
- [ ] 用 `--fixture-set expanded_100` 验证解析、分块、Embedding、向量、BM25、registry 元数据一致。
- [ ] 20k staging 批量入库完成后核对文档数、chunk 数、失败数、重试数、PG 连接与队列积压。
- [ ] 重跑同一批次后 active registry、向量、chunk_store、BM25 不重复增长。
- [ ] 扫描件只有在 OCR 健康检查通过后才使用 `--include-scanned`。

## 检索质量与性能

- [ ] `python -m evaluation rag --selection pr_baseline --no-ragas --no-resume` 通过。
- [ ] `python -m evaluation rag --selection expanded_100 --multiquery --no-ragas --no-resume` 通过。
- [ ] `python -m evaluation rag --selection scale_20k --multiquery --no-ragas --no-resume` 在 staging 完成。
- [ ] 记录 Recall@5/10、MRR、NDCG@10、Top-1、reject accuracy、版本/部门隔离、错误率。
- [ ] 记录导入耗时、Embedding 吞吐、内存、P50/P95/P99 检索延迟。
- [ ] 失败用例能在 `per_case/<case_id>.json` 定位证据和 scope，不以 pass_rate 单指标放行。

## 上线前必须验证

- 用真实 2 万份文档完成一次增量入库，记录解析失败、空 chunk、embedding 失败、BM25 同步失败和任务重试数。
- 用业务标注问答集至少 300～1000 条测量 Recall@5、MRR、Top-1、Evidence Gate 误拒率。
- 按文档类型、文件格式、知识库和权限主体分层统计，不只看总体平均数。
- 压测查询 QPS、P50/P95/P99 延迟、PG 连接占用、BM25 进程内存和 Celery `rag_index` 队列长度。
- 跑一次一致性检查，确认 registry、PGVector doc/chunk、PG chunk_store、BM25 的 doc_id 集合一致。

## 重点观察

- Stage 1 `RAG_DOC_CANDIDATE_K` 提高后，若 Recall@5 上升但 P95 过高，优先优化 doc-level 索引和查询过滤，不要无限增大候选数。
- BM25 过采样解决的是“全局 Top-K 被其他 KB 挤掉”的问题，不会修复 PDF 图片、复杂表格或 OCR 未识别造成的缺文本。
- 人名索引在上传/重索引后会随 BM25 刷新；若 doc_db 写入失败，必须以一致性告警为准，不能把空索引当成“没有相关文档”。
- `VECTOR_HNSW_EF_SEARCH` 只对当前事务生效，连接归还连接池后不会污染其他请求。

## 报告与故障恢复

- [ ] 报告目录为 `data/eval_runs/<run_id>/`，至少包含 `report.json`、`meta.json`、`per_case/`。
- [ ] 中途停止时保留 `results_checkpoint.jsonl`，同一 run 目录重跑启用 resume。
- [ ] 任务阶段状态记录在 `.superpowers/sdd/rag-eval-kb-unification/progress.md`，失败阶段报告为 `task-N-report.md`。
- [ ] 修复后从第一个未完成阶段重跑，并重新执行该阶段及其依赖阶段的测试。

## 放行条件

- [ ] 不存在跨 KB/跨 fixture_set 的召回证据。
- [ ] 失败、跳过和错误数量均有解释，不把 skip/error 计入成功率分母外的“通过”。
- [ ] 所有阈值、报告路径和运行参数已归档，可按 run_id 复现。

## 不能直接承诺的指标

现有约 100 份文档评测结果不能外推为 2 万份文档的线上成功率。上线结论必须来自同版本、同 embedding、同权限过滤、同查询分布的规模化真实数据；若没有标注问答集，只能报告延迟、空召回率和人工抽检结果，不能声称检索成功率达到某个百分比。
