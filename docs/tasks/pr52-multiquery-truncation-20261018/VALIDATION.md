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

## Round 4：超时根因、迟到用量和同模型最小优化（无付费请求）

### 冻结的真实账目与逐条可核实范围

唯一真实 Smoke 仍是 [39108656690](https://github.com/Jayus-07/agent/actions/runs/39108656690)，对应评测运行 `rag-smoke-39108656690-1`、预算请求 `eval:rag-smoke-39108656690-1`。账本 Artifact 只保留按组件聚合的 5 条 `needs_review`，没有逐行 ID 或可连接到 Trace 的 reservation/call ID。下表中的 A/B/C 是本报告占位符，不是账本标识；不会伪造关联：

| 账目占位符 | 可恢复的聚合身份 | Provider / 时间线证据 | 原始用量、价格与结算 | 逐调用结论 |
| --- | --- | --- | --- | --- |
| Chat A | `llm` / `doubao-seed-2.0-mini` / `rag-eval-chat`；聚合分组为 3 条 unpriced | RC-024 Trace 有约 6001ms 改写等待超时；缺 row/call/request ID，不能证明就是此行 | 无逐行 usage；聚合 billed cost 为 NULL、`pricing_source=unpriced`；reservation ID 与预占状态无法逐行恢复 | 请求是否发出、Provider 是否完成/迟到、是否计费均无法确认；保留 `needs_review` |
| Chat B | 同上 | RC-031 Trace 有约 6001ms 改写等待超时；缺可关联 ID，不能证明就是此行 | 同上；Prompt key/version/source/hash 对此旧调用不可恢复 | 请求完成和迟到 usage 均无法确认；保留 `needs_review` |
| Chat C | 同上 | Artifact 未提供可唯一关联的第三个 Chat Trace | 同上 | 调用前失败、Provider 错误或迟到响应无法区分；保留 `needs_review` |
| Rerank A | `rerank` / `qwen3.7-text-rerank` / `rag-eval-rerank`；聚合分组为 2 条 unpriced | RC-031 有约 1.90784s DashScope `ReadTimeout` Trace，但没有 row/call/reservation ID，不能映射到 A 或 B | 无逐行 Provider usage；聚合 billed cost 为 NULL、pricing source unknown | 该 Trace 证明客户端读超时，不证明 Provider 未处理或未计费；保留 `needs_review` |
| Rerank B | 同上 | 该 Artifact 没有第二条可唯一匹配的异常 Trace | 同上 | 是否发出、完成或迟到均无法确认；保留 `needs_review` |

所有 5 行缺失字段相同：usage record ID、reservation ID、内部 call ID、单请求 request/trace ID、Provider request ID、逐请求开始/结束时间、异常对象、Provider 响应与原始 usage payload、价格版本快照、Prompt 元数据。Artifact 仅有运行级 eval/request ID。汇总金额为 settled 108 条 CNY 0.117488；`needs_review` 预占 CNY 1.25、已结算金额 CNY 0.00。CNY 1.25 是未核实记录的预占额，不是实际费用；整轮仍为 `cost_verification=UNVERIFIED`。没有重新发起模型请求，也没有将未知费用填成 0 或改为 VERIFIED。

### 超时配置来源与运行时阶段

- 历史 Trace 的改写等待为约 6001ms；RC-024 根链 7754ms，改写后余约246ms，Rerank 约1642ms；RC-031 根链8064ms，Rerank约1971ms后失败。历史有效改写子预算为6000ms、总 deadline 为8000ms。
- 当前代码默认 `RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS=6000`，RAG Smoke PR workflow 未覆盖该变量；`multi_query` 角色默认策略为10秒。历史运行的 runner 环境值和数据库角色策略快照未随Artifact导出，因此“来自代码默认”是最符合现存证据的解释，但不是可证实的历史事实。
- 本轮没有延长 timeout、总 deadline 或调整 Token 上限。改写 span 现在记 `retrieval_deadline_ms`、配置/有效 timeout 与来源、角色 timeout 与来源、deadline 前后剩余量及 timeout kind。Stage 分解使用 monotonic clock：排队、worker start、角色解析、模型解析、Prompt 渲染、价格预检、预算预占、Provider call/response、输出解析、Trace 收尾和总耗时。Chat SDK 不暴露首包时间，`provider_ttfb_ms=unavailable`；DashScope Requests adapter 才使用 `Response.elapsed`。
- 超时主 span 由调用方冻结一次；线程继续运行时只写关联 `query_rewrite_late` span。Prompt 渲染完成立即写本次 `rag.multi_query` 的 key/version/source/rendered hash；渲染前短路明确 `prompt_status=not_rendered`。Trace 只记录允许的短 Provider request ID 和用量/价格状态，不写完整 Prompt、查询、凭据或 `_runtime_mq_queries`。
- 取消分为调用方 timeout、Future 是否成功取消、worker 是否结束、Provider 是否完成/确认取消、usage 是否收到和预算实际结算状态。同步 Provider I/O 不宣称可被 `Future.cancel()` 或 Event 物理取消。迟到 usage 使用原 request context、reservation 和调用身份结算；业务超时 span 不会被迟到结果改写。

### 免费验证

- 本机使用新建、独立的 `pr52-rag-review-pg`（127.0.0.1:55452、pgvector/pg16），与共享业务库隔离；`scripts/init_db.py` 在该容器中 rc=0。pgvector 集成测试实际执行，没有跳过。
- `python -m backend.evaluation.preflight --module rag --selection pr_smoke`：PASS，8 cases，suite hash `8edad96827b879c3`。
- 完整 RAG PR 免费 pytest 集合（包括 `backend/tests/prompts/`、16-fixture 完整性、PostgreSQL/pgvector SQL 集成、预算/usage identity 与新 Fake Provider 测试）：624 passed，56.28s，0 skipped。pytest 汇总2个 evaluation `TestCase` collection warning；另在 Windows asyncio Proactor 清理时观察到既有 `Event loop is closed` unraisable warning；pytest exit code 0。
- 最终直接竞态/账本子集：14 passed / 7.58s；另验证结算失败会保留 `needs_review` 而不是记成已结算。全量第一次执行的两个新测试失败分别是旧断言未随状态名更新和队列 Timer 留给 worker 的时间不足；断言及测试同步时序修正后完整集合重跑通过。
- `py_compile`：通过；Ruff `--select E4,E7,E9,F63,F7,F82`：通过；`git diff --check`：通过。没有发出 Provider 调用，没有触发真实 Smoke。
- GitHub [RAG PR Smoke 39127335803](https://github.com/Jayus-07/agent/actions/runs/39127335803)：PASS，E0 8 cases，免费回归 624 passed / 35.31s，真实 API evaluation job `SKIPPED_UNTRUSTED_PR`。workflow event 是 `pull_request`，没有创建付费调用。
- GitHub [Contract Quality Check 39127335772](https://github.com/Jayus-07/agent/actions/runs/39127335772)：PASS，Tool contract `IN_SYNC`、资产一致性 56 passed、Tool duplicate check PASS、Prompt Lock 57 prompts。另手动触发的同 HEAD [39127349870](https://github.com/Jayus-07/agent/actions/runs/39127349870) 也 PASS。
- GitHub [Secret Scan 39127335795](https://github.com/Jayus-07/agent/actions/runs/39127335795)：工作树、新增行和提交区间历史扫描均 PASS。

### 同模型优化判断与未决事项

现存证据只证明改写等待吃掉接近全部子预算，不能把耗时可靠拆成排队、治理前置、网络或模型推理，也不能用旧超时 Trace 确认 Provider 完成/计费。Fake Provider 证明阶段指标和迟到结算链路，不作为模型性能数据。因此本轮不改模型参数、Prompt、timeout 或 deadline。用户另行指出的同模型关闭思考提交 `04d72f18ad8476aab51c1612060461f529d415eb` 仍在独立分支 `codex/llm-thinking-unify`；它不属于本 PR 分支、本轮未 cherry-pick，也尚未用真实质量/用量数据验证。若要验证是否为最小提速方案，需要下一次获批的受控真实调用，并继续复核质量与供应商账单。

本轮硬约束：**不执行真实付费 Smoke、不发布 Prompt、不回滚、不解除 Draft、不合并**。5 条记录维持 `needs_review` / `UNVERIFIED`，历史 timeout 来源仍未完全确认；因此 `MERGE_READY=false`。

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
