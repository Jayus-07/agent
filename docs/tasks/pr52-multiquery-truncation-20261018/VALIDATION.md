# 实际验证命令与结果

实现提交：`a87df8316f820fa5cd008ec24fcab2d568022a88`。Python 3.10.2，本机PG为独立容器`pr52-round3-pg`、127.0.0.1:55452，测试库agent_memory_test/agent_business_test；日志与临时脚本在`D:/tmp/pr52-round3-20261018/`。没有写共享业务库。

## T0

- 改动Python逐文件`py_compile.compile(..., doraise=True)`：PASS，编译输出位于上述临时目录。
- `git diff --check`：PASS。
- `D:/Python/python.exe -m ruff check backend/rag/chain.py backend/rag/indexing/indexer.py backend/rag/preprocessing/keyword.py backend/rag/indexing/lifecycle.py backend/rag/reranker.py backend/rag/metrics.py --select F811,F821`：PASS。
- 新增completion/review_hardening测试及chunk_cache/retrieval_budget/renderer定向Ruff：PASS。
- `D:/Python/python.exe -m backend.evaluation.preflight --module rag --selection pr_smoke`：PASS，8 cases，suite hash=`8edad96827b879c3`。

## T1/T2

本机通过临时`free_tests.py`为子进程注入隔离PG环境，从workflow读取并执行其完整免费pytest命令；最终509项集合通过后，新增36项BM25兼容集合通过。最终GitHub执行两者并集，结果**545 passed / 35.92s，0 skipped**。

实际免费CI命令（与本机底层pytest参数相同；本机解释器为`D:/Python/python.exe`）：

```bash
python -m pytest tests/test_rag_smoke_path_filter.py \
  tests/evaluation/test_seed_eval_bindings_verify.py \
  tests/evaluation/test_release_gate_tier_samples.py \
  tests/evaluation/test_rag_ci_workflow_contracts.py \
  tests/evaluation/test_rag_runner.py \
  backend/tests/rag/test_specialized_budget.py \
  backend/tests/evaluation/test_import_fixture_entrypoint.py \
  backend/tests/evaluation/test_rag_paid_preflight.py \
  backend/tests/scripts/test_seed_prompt_eval_db.py \
  backend/tests/rag/test_rag_fixture_content_integrity.py \
  backend/tests/rag/test_rag_cleaning_audit_boundaries.py \
  backend/tests/rag/test_reindex_failure_snapshot.py \
  backend/tests/rag/test_multi_query_token_budget.py \
  backend/tests/rag/test_multi_query_rewrite_completion.py \
  backend/tests/rag/test_multi_query_rerank.py \
  backend/tests/rag/test_multi_query_runtime_queries.py \
  backend/tests/rag/test_review_hardening.py \
  backend/tests/rag/test_pgvector_store.py \
  backend/tests/rag/test_bm25_identity_and_refresh.py \
  backend/tests/rag/test_bm25_sync.py \
  backend/tests/rag/test_bm25_stale_fix.py \
  backend/tests/rag/test_bm25_vector_rebuild.py \
  backend/tests/rag/test_rag_index_reconcile.py \
  backend/tests/test_indexer_bm25_sync.py \
  backend/tests/rag/test_contextual_prefix.py \
  backend/tests/api/test_rag_server_internal_token.py \
  backend/tests/test_rag_remote_mode.py \
  backend/tests/prompts/ -q --no-cov
```

- [GitHub免费CI 39108008370](https://github.com/Jayus-07/agent/actions/runs/39108008370)：上述545项通过；Contract Check、Secret Scan通过。
- 本机509项组合：51.81s，包含完整`backend/tests/prompts/`（仓库没有根级`tests/prompts/`）、16份fixture离线完整性、SQL纯函数与真实PG执行、JSON缓存实际文件读写、FastAPI TestClient/HTTP MockTransport埋点、治理/预算/装配边界。
- BM25补充36项：23.14s，命令为上述五个BM25/reconcile文件单独执行。
- 另有受影响MQ/身份/Trace集合324 passed/51.56s；评测入口/小候选边界29 passed/24.97s；最终completion/子预算集合23 passed/5.78s。
- 原Prompt alias单测mock旧bump函数导致失败，按现行stage/dispatch契约修复并新增await断言，完整Prompt集合已实际通过。初次本机PG缺省连接挂起已终止；隔离PG迁移补齐兼容库后rc=0，无真实必要门禁被静默skip。
- 尝试`--timeout`参数时因本机无pytest-timeout插件而未运行；未安装依赖或把它计作通过。Windows异步PG清理阶段仍可见既有Event loop is closed告警。

## T3：FAIL / UNVERIFIED，停止

实际创建run的命令：

```powershell
gh api --method POST repos/Jayus-07/agent/actions/workflows/rag_smoke.yml/dispatches --input D:/tmp/pr52-round3-20261018/dispatch.json
```

请求体为原分支及`inputs.multiquery=true`。仅创建[39108656690](https://github.com/Jayus-07/agent/actions/runs/39108656690)这一个本轮真实run。

- fixture16/16、索引完整性PASS；严格Top-1与账本门禁FAIL。
- [逐例查询/排序/引用/指标/账本与根因](SMOKE.md)。Provider响应完成信息、有效三查询、完整评分及5条未知用量均未验收。
- 治理状态只读SQL：Active v1/hash=`d3ea6cc684a20879`，v2 draft/hash=`898e7970527161cf`；57项Active与Lock逐项比较0差异。未发布或执行真实回滚，二者不计为通过。
- 新增前置耗时埋点已通过本地HTTP实际运行，未在生产请求上测得历史4秒缺口的新归因；该生产测量仍未验收。
- BM25仅完成报告允许的最小“缺校验拒绝”与说明修正，SHA256不提供抗目录任意写来源认证；chunk缓存已使用非执行JSON。
