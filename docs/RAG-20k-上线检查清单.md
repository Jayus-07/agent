# RAG 20k 上线检查清单

## 目标与边界

- [ ] 20k 文档只在独立 staging 验证，不直接污染生产 KB。
- [ ] 评测写入 `kb_id=rag_eval_kb`，运行范围为 `fixture_set=scale_20k`。
- [ ] `rag_test_kb`、`rag_100_docs` 只读兼容，不再有新写入。
- [ ] 测试 KB 不出现在 customer/employee 的授权集合中。

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

## 报告与故障恢复

- [ ] 报告目录为 `data/eval_runs/<run_id>/`，至少包含 `report.json`、`meta.json`、`per_case/`。
- [ ] 中途停止时保留 `results_checkpoint.jsonl`，同一 run 目录重跑启用 resume。
- [ ] 任务阶段状态记录在 `.superpowers/sdd/rag-eval-kb-unification/progress.md`，失败阶段报告为 `task-N-report.md`。
- [ ] 修复后从第一个未完成阶段重跑，并重新执行该阶段及其依赖阶段的测试。

## 放行条件

- [ ] 不存在跨 KB/跨 fixture_set 的召回证据。
- [ ] 失败、跳过和错误数量均有解释，不把 skip/error 计入成功率分母外的“通过”。
- [ ] 所有阈值、报告路径和运行参数已归档，可按 run_id 复现。
