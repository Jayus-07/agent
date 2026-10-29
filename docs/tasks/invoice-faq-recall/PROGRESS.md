# PROGRESS: invoice-faq-recall

> 配套 TASK.md。进展只记本文件，无证据不标完成。

## 已知证据（来自任务书与上一专项，开工时可直接引用）

- 症状：「企业申请发票需要税号和开户银行吗？请给出处。」（POST :3200/api/chat/agent/stream）→ 拒答「知识库中未找到」。
- 数据在库：chunk_store e07b25dc7b_14 =「Q：能开发票抬头吗? A：支持个人/企业抬头，需提供税号及开户银行（企业)」；同文档 _5 发票开具、_1 支付、_0 发货。
- 10-27 18:49 同措辞成功（trace 07c85a58dc1d，相关度 0.98）；10-27 19:17 起持续全失败（近 3000 条 trace 扫描 44 次全失败）。
- 最近失败（10-29，trace ea7b04f4192f，rag 子 6baa206c6d5f）：single_query，融合候选 8 块 rerank 全部 <0.3 被截 → no_evidence 拒答；eviction 全是报销主题块。
- 10-28 失败（trace 9ce5e7eb33a0，rag 子 823d942c5510）：MQ 改写触发（multi_query、10 块候选 output=0）——改写救不了，目标块连候选池都进不去。
- 根因定性：召回丢失。链路=增强混合检索（dense 9-10 / sparse 83-98 命中）→ 加权 RRF（dense 3.0 / rule 2.0 / sparse 1.0）截 top_k 8-10 → rerank → Gate。发票问与报销主题文档词面高度重叠，RRF 下被高频词文档挤出头部。
- 待复核翻案项：_14 向量可能是旧模型产出（doc_registry 该文档 embedding_model 记录混杂 qwen3.7-text-embedding/空），当前绑定 bge-m3。

## 进展

### 2026-10-30 开工与 P0（含计划外环境故障修复）

- [x] 读 AGENTS.md + agent-repo-pitfalls skill；新建工作树（分支 codex/invoice-faq-recall，base=d9c6100）。
- [x] 建 TASK.md / PROGRESS.md。
- [x] P0 栈健康：docker ps 主栈全 healthy（app :8000 / rag-service :8090 / pg :5433 / BFF :3200）。
- **计划外发现与修复（重要）：embedding 角色绑定被测试残留数据污染，所有依赖 embedding 的链路全挂。**
  - 初次复现（trace bebd6cdac651、d92b7d9dfd0e、bf04e133fca5）：发票问与发货问均走 clarification 拒答，
    领域判断 `source=fallback`、「记忆检索向量化」span `APIConnectionError`，app 日志
    `[Embedding] Cloud 模式初始化完成 (model=new, api_base=http://127.0.0.1:51346)`。
  - 根因：`backend/tests/rag/test_embedding_migration_worker.py` 用 `migration-test-<uuid8>` 供应商 +
    model 'new' + 死端口 51346 临时劫持 embedding 绑定，finally 恢复并清理；**10-29 01:19 该测试中途
    崩溃（provider `migration-test-1bd479f7` 残留在 llm_providers/llm_models，未清理）**，
    embedding 绑定残留在 'new'→死端口。宿主机 51346 无监听，容器内必 Connection error。
  - 修复：`UPDATE llm_model_role_bindings SET model_name='bge-m3', updated_by='task:invoice-faq-recall'`
    （单行恢复既有正确值）。bge-m3 挂 custom-gpu（host.docker.internal:8088 本地 GPU 服务，
    路径 /embeddings dim=1024，容器内实测 OK，/healthz 显示 embedder+reranker 已加载）。
    **残留垃圾 provider `migration-test-1bd479f7` + model 'new' 的删除留待用户确认（不可逆）。**
- [x] P0 复现（绑定修复后，症状与任务书吻合）：trace 33153ba607db（父）→ rag 子 42ce242ebdb8，
    20.9s 返回「知识库中未找到与该问题相关的内容。」路由已恢复 knowledge（pred=knowledge, margin 0.975）；
    rag 链路：LLM改写 variants=0、MQ 未触发 → 增强混合检索 dense_hits=9 / sparse_hits=83 / final_k=9 /
    overall_confidence=0.602 → Chunk 检索 8 块 → rerank（bge-reranker-v2-m3 成功调用 1.2s）后 0 块存活 →
    Evidence Gate retrieval 层 `no_evidence` 拒答（doc_count=0）。
  - 证据文件：D:/tmp/invoice-faq-recall/{p0_after_fix_sse.txt, p0_after_fix_trace.json, rag_child_trace.json,
    p0_repro_sse.txt, p0_repro2_trace.json, p0_ship_check_sse.txt}；脚本 login.sh / ask_invoice.py / get_trace.py。
- 与任务书差异记录：任务书 10-29 失败 trace 中 rerank 是「8 块全部 <0.3 被截」；本次复现 rerank 后
  doc_count=0（Gate 层同 no_evidence）——一致语义：目标块不在候选池，rerank 池里全是报销主题块。

### P1 结论（2026-10-30，两项复核完成，根因推翻任务书原定性）

1. **向量新鲜度复核：无问题（翻案失败）**。e07b25dc7b 全部 18 块 created_at=2026-10-27 20:03 同批、
   metadata embedding_model 全=bge-m3；rag_index_meta chroma collection=bge-m3/1024 维/ready
   （built_at 2026-10-27 22:59，revision 9b0cdc11）。
2. **融合探针实锤真正根因：HNSW 索引召回丢失（ef_search 不足），非 RRF 竞争力**。
   - 精确检索（全表排序）：_14 全局 #6，sim=0.6219；精确 top9 = 04a34059af:5(0.6595) /
     35f8f4054f:2 / 04a34059af:6 / 2e41f875cc:5 / 190a410e06:8 / **e07b25dc7b:14(0.6219)** /
     35f8f4054f:4 / **e07b25dc7b:5(0.6142)** / ccfae36319:3。
   - HNSW 索引检索（`ORDER BY embedding <=> … LIMIT`）：ef_search=40 与 **80（当前默认）均丢块**
     （top9 全是无关块，_14 不在）；**ef≥90 起 top9 与精确检索一致**（pgvector 0.8.3，实测）。
   - 真实链路每次执行 `SET LOCAL hnsw.ef_search=80`（pgvector_store.py:859，VECTOR_HNSW_EF_SEARCH
     默认 80）→ dense 路 9 个 3.0 权重名额全给无关块；_14 只拿 BM25 第 1 名的 1.0 权重
     （RRF 0.0164 < dense 第 9 名 0.0435）→ 融合落选 → rerank 池全是报销/表格块全截 → no_evidence。
   - sparse 路 _14 本就是 BM25 第 1 名（探针 [3]），BM25 无问题。
   - 时间线吻合：10-27 20:03 文档重建 + 22:59 索引重建后 HNSW 图下 ef=80 召回开始丢块；
     10-27 18:49 成功发生在旧索引上。「bge-m3 下排序竞争力不足」的原定性不成立——查询向量对
     _14 的 sim=0.62 反而是所有候选里文档语义最贴的。

### P2 实现（2026-10-30）

- `backend/config/rag.py`：VECTOR_HNSW_EF_SEARCH 默认 80→100（单参数、env 可覆盖、立即回滚可逆；
  5440 块规模 ef=100 延迟增量可忽略）。融合权重/阈值/Gate/prompt 全部未动。
- `backend/tests/rag/test_pgvector_pool.py`：新增 test_vector_hnsw_ef_search_default_at_least_90
  回归锁（PASS）。
- 测试结果：本工作树 `pytest backend/tests/rag/test_pgvector_pool.py` = 1 failed + 2 passed；
  该 failed（test_similarity_search_sets_transaction_local_hnsw_ef，FakeCursor 缺 fetchone）为
  **既有失败**——主仓库 d9c6100 基线同样 1 failed + 1 passed，与本改动无关，不越界修。

### P3 验证（2026-10-30，全部完成）

**重要更正**：调查中发现 17:49-17:59 期间的 rebuild 存在 Docker 层缓存/时序问题——
v3/v5/v7 轮次（当时记为「K=100」）实际 doc_ids 仍=45（RAG_DOC_CANDIDATE_K=100 未进镜像），
18:20 rebuild 后 doc_ids=91 才真正生效。据此：
- v5/v7 书面问 3/3 FAIL 发生在「doc_ids=45 + ef=100」状态，其 FAIL 主因是 MQ 改写随机 +
  rerank GPU 单并发拒绝（trace 9597409dd053：rerank_provider_attempt#2 error 26ms
  usage_status=unavailable，inference_concurrency=1/max_inflight=2，MQ 3 变体并发 rerank
  必有一路被拒），与 doc K 的因果不成立（v6/v8 的 K=50 PASS 同为 doc_ids=45）。
- v9 起（doc_ids=91）书面问 2/2 PASS——**doc K 加深并未破坏书面问**。

**验收矩阵（真实 SSE + trace，D:/tmp/invoice-faq-recall/ 全部留档）**：

| 验收项 | 结果 | trace | 耗时 |
|---|---|---|---|
| 1. 发票问（税号+开户银行+e07b25dc7b 出处） | PASS ×2（相关度 0.89/0.94） | e87f914aa33e / 292edf5758bb | 5.9s / 3.7s |
| 2a. 发货口语（24 小时+出处） | PASS（0.99） | f0efc48739a7 | 5.0s |
| 2b. 发货书面（24 小时+出处） | PASS ×2 | d1c63ff08246 / b7a7f2108b85 | 9.2s / 9.3s |
| 2c. 支付问（支付宝/微信/银联+出处） | PASS（1.00） | b840f266296d | 4.0s |
| 2d. 「用一句话解释幂等」general_chat 直答 | PASS（直答无检索） | 545ebeacd6c6 | 1.4s |
| 3. 耗时档 5-12s（P95 视角） | 全部在档（书面问 9.2/9.3s 与上一专项 7.2s 同档，MQ 路径固有） | — | 见上 |

**rag 评测门禁（离线判定口径，宿主机 + eval_wrapper.py 进程内 DNS 映射
host.docker.internal→127.0.0.1，因本机 GPU 服务只绑 127.0.0.1；容器内评测 OOM 不可用）**：

| 套件 | 本改动 | 主仓库基线 d9c6100（同法复跑） |
|---|---|---|
| smoke 8 | 语义召回 **1.0000**，通过率 25% | 语义召回 1.0000，通过率 25% |
| core 16 | 语义召回 **0.9375**，通过率 43.8% | 语义召回 0.9375，通过率 43.8% |

- 通过率与基线**逐位持平 → 检索层零回归**。离线通过率低于历史 24/24 的原因：历史 24/24 是
  prompt 发布评测（--live + judge）口径；离线判定按「期望来源文件名 vs 实际 Top-1」精确匹配，
  faq_only.md↔faq_only.docx 等后缀不匹配是上一专项已知的 DATA-01 历史遗留（PROGRESS 续2 记录），
  与本改动无关（run 目录：worktree data/eval_runs/2026-10-30T02-37-15、2026-10-30T02-41-24；
  基线对照 D:/Program Files/workplace/agent/data/eval_runs/2026-10-30T02-39-20、02-44-15）。
- --live 付费评测未跑（涉及计费，需用户确认后另行执行）。

### P4 收尾状态

- 改动清单（未提交，待用户确认）：
  - backend/config/rag.py：VECTOR_HNSW_EF_SEARCH 默认 80→100；RAG_DOC_CANDIDATE_K 默认 50→100（各带根因注释）
  - backend/tests/rag/test_pgvector_pool.py：新增 2 个默认值回归锁（PASS）
  - docs/tasks/invoice-faq-recall/：TASK.md + PROGRESS.md
- 既有失败（非本次引入，基线复现）：test_pgvector_pool.py::test_similarity_search_sets_transaction_local_hnsw_ef
  （FakeCursor 桩缺 fetchone，主仓库 d9c6100 同样失败）；test_hybrid_gate_score_scale 2 处（上一专项已记录，环境敏感）。
- 评测辅助产物（gitignore，不入库）：backend/data/bm25（docker cp 自容器卷）、data/eval_runs/。
- 主栈当前状态：app/rag-service 已从本 worktree 重建（K=100 生效，doc_ids=91），与改动一致；
  诊断打点已删除并重建验证。

## 阻塞/待确认（已处理）

1. ~~测试残留数据清理~~ **已获用户确认（2026-10-30）并执行**：删除 llm_providers 行
   `migration-test-1bd479f7`、llm_models 行 `name='new'` 及其关联凭据。
2. test_embedding_migration_worker 的防残留加固（测试 finally 已有清理逻辑，但崩溃时无效——
   可考虑用独立 PG 库/事务回滚隔离），建议另开专项，本次不扩大范围。

## 下一步

提交 → 删除垃圾数据（已确认）→ 合并 main（推送走代理）。
