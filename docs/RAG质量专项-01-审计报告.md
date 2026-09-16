# RAG 质量专项 · R0 架构审计报告（2026-09-17）

> 对应任务：`docs/未完成功能进度汇总-2026-09-16.md` 附录二 **R0 审计与基线**（C20 第一步）。
> 审计边界：**检索质量侧增量审计**。上传链路可靠性（落盘/锁/Celery/部分写入/SSE）已由 `docs/rag-upload-fault-analysis-2026-09-17.md`（FMEA F1-F10）覆盖，本文不重复审计，仅引用其结论。
> 基线 HEAD：`83cd24b`。本文所有「实测」均可按文中命令与 file:line 复现。

---

## 一、链路事实（带证据）

### 1.1 上传 → 索引主链路

`POST /api/rag/upload`（`backend/app/api/routes/rag_upload.py`）→ 落盘 + SHA256 + 文件锁 + duplicate 短路 → Celery `rag_index` 队列（`backend/tasks/index_tasks.py`，broker 不可达回退进程内）→ `IncrementalIndexer._index_file`（`backend/rag/indexing/indexer.py:343`）→ `parse_and_chunk`（`backend/rag/preprocessing/pipeline.py:21`）→ ChunkFilter 过滤（indexer.py:588-603）→ LLM 元数据（indexer.py:640-647）→ chunk_store 写入（783-802）→ doc_db 写入（821-840）→ 预嵌入（891-907）→ Chroma 写入（909-944）→ BM25 replace（946-958）→ registry register（960-1006）→ SSE 终态。

Trace 树 8 span（upload/load/parse/clean/dedup/chunk/metadata/embed/vector_db），截断与降级均有 trace 留痕。可靠性细节见 FMEA：0-chunk 门禁（FMEA P1-4，indexer.py:85,487,965-971）、F4 部分写入精确清理（indexer.py:1262-1321）、中断恢复（indexer.py:193-243）均已实现。

### 1.2 ID 与版本语义（实测）

| 对象 | 实际定义 | 证据 | 含义 |
|---|---|---|---|
| doc_id | `md5(f"{kb_id}\|{department}\|{subpath}\|{basename}")[:10]` | doc_id.py:38-52 | **路径身份，不含内容 hash**。同路径不同内容=同 doc_id（版本覆盖语义）；同内容不同路径=不同 doc_id |
| chunk_id | `f"{doc_id}_{i}"`（位置序号） | indexer.py:740 | **位置派生，非内容派生**。切分参数变化 → 全量 chunk_id 漂移。⚠️ indexer.py:514 注释称"chunk_id 为内容派生"与实现不符 |
| doc_version | 整数计数器，重索引 +1 | doc_registry.py:495-521 | 无版本历史表；旧版本不可查询 |
| 内容去重 | SHA256 文件级 dedup + MinHash 近似重复 → pending_review | doc_registry.py:363-366 | 近似重复不静默入库 ✅ |

### 1.3 registry（SQLite，= C19 证据）

Schema 30 列（doc_registry.py:23-60），**主键为 file_path**；状态机 7 态含 pending_review（:21）；`threading.Lock` 仅进程内有效（:75），多进程（app + worker）并发写依赖 SQLite 文件锁。**任务书 §6 要求的字段缺口**：无 `version_id`、`effective_from/effective_to`、`supersedes_version_id`、`source_priority`、`quality_status`（有 quality_score/issues 但非状态位）。版本快照机制仅覆盖 financial 文档（indexer.py:1240-1258，`is_latest=False` 保留旧向量），其余 doc_type 重索引 = 旧版本物理删除。

### 1.4 内容完整性与可追溯（任务书 §5 对照）

已满足 ✅：0-chunk 必失败（ChunkingEmptyError）；截断留痕进 registry `quality_issues`（indexer.py:651-656）；doc 级 16K 截断打标（:828-830）；ChunkFilter 过滤原因写入 metadata（filter.py:162-233，6 类原因）+ 计数进 trace（indexer.py:601-602）；近重复 → pending_review。

缺口 ❌：
1. **清洗原地覆盖、原文不留存**：`pipeline.py:57-59` 直接 `node.text = cleaner.clean(...)`，无 `raw_text`/`clean_text` 分离、无 `cleaning_operations` 记录 → 任务书 5.2「原文可追溯」不满足。URL/邮箱等被清洗改写后无法回溯原文。
2. **被过滤 chunk 未持久化明细**：只有 debug 日志 + 总数（indexer.py:598-602），被过滤文本本身丢失，无法事后审计「是否误删有业务意义的短文本」。
3. **无入库质量报告 JSON**：各阶段指标散在 trace/registry，没有任务书 5.1 要求的每文档一份结构化质量报告。

### 1.5 四库/五路一致性

`IndexConsistencyChecker`（consistency.py:87-405）覆盖 5 项检查（registry↔磁盘、Chroma 孤儿/缺失、doc_db、chunk_store、BM25 计数），Sweeper 周期对账+修复+Prometheus 打点（:408-445），BM25 缺失可从 chunk_store 重建（:159-192）。**满足任务书「不允许静默不一致」的基本盘**。
规模隐患：checker 用 `vectordb.get()` 全量拉取（:229-235,253-259）——2 万份规模下对账成本随总量线性膨胀（P2）。

### 1.6 检索链路

- 混合召回 RRF：`hybrid.py:413-431`（vector + BM25 + 规则多路，rrf_k=60）；
- 多路编排 + 置信度聚合 + 低置信扩展：`enhanced_hybrid_retrieval.py:139-161,265`；
- kb_id 过滤在召回前下推 Chroma where：`kb_filter.py:12-28` ✅（任务书 12.4 前置过滤满足）；
- Parent 扩展：`retrievers.py:88`；查询分析：`query_analyzer.py:182`；Rerank：`reranker.py`；
- 逐阶段 trace 事件（`rrf_fusion` 等，hybrid.py:436-451）——任务书 §7 可观测要求基本具备，缺「拒答原因」结构化输出（evidence_gate 已有 controller，需确认输出契约，留给 R4）。

### 1.7 评测体系

入口 `python -m backend.evaluation rag --smoke/--live/--judge/--compare`（cli.py:52-80，自带回归门禁 `gate/` + Markdown 报告）。数据集 `rag_test_kb.json`：**103 例**，schema 4.0，含 `required_facts`/`should_reject`/`required_docs`/`query_type`（single_doc 等）/`tier`（smoke）。
**标注缺口（任务书 §4 对照）**：`required_docs` 按**文件名**引用（如 `faq_常见问题FAQ.md`），无 doc_id 级标注、无 expected_chunk_ids、无版本/权限维度用例。

---

## 二、基线实测（2026-09-17 01:40-01:50）

### 2.1 定向测试 ✅

```
命令: ./.venv/Scripts/python.exe -m pytest backend/tests/rag/ backend/tests/config/ \
      backend/tests/test_rag_upload_celery_mode.py backend/tests/test_indexer_recovery_and_reindex.py \
      backend/tests/test_upload_resilience.py -q -p no:randomly --no-cov
结果: 512 例 = 510 passed / 2 skipped / 0 failed（2m06s）
日志: logs/r0_baseline_tests.log
```

**R2 修复的回归基线：510 passed，不得低于此数。**

### 2.2 评测基线 ❌ 不可得（如实记录，不伪造）

```
命令: ./.venv/Scripts/python.exe -m backend.evaluation rag --dataset rag_test_kb.json --smoke
结果: 两次均 exit 139（Segmentation fault），崩点一致——
      加载 data/chroma、data/doc_db 后，执行 "[Recovery] 发现 1 个中断文档" 时崩溃
日志: logs/r0_eval_smoke.log / r0_eval_smoke2.log
```

配套数据现状（只读核查 `data/doc_registry.db`）：**active 仅 3 行 + 1 行卡 `parsing`**（`legal_采购合同.md`）——rag_test_kb 语料在本工作副本未完整索引；该 parsing 卡住文档即评测启动恢复的崩溃触发点（毒丸文档，疑似）。

**结论**：Recall@5 / MRR / NDCG / 拒答准确率基线当前不可得。R2 前置事项：① 修复评测进程段错误（原生库层面，疑 torch/chroma 冲突，需单独定位）；② 清理/重索引 parsing 卡住文档；③ 重建 rag_test_kb 语料索引。之后才能跑出可信基线。

### 2.2.1 ⭐ R2 补记（2026-09-17 02:30）：段错误已修复，基线已取得

**R-P0-1 根因（实测定位）**：`langchain_text_splitters`（顶层拉起 `sentence_transformers`→torch）在 chroma/doc_db 等原生库已加载后被惰性导入（`indexer.py` 内 `parse_and_chunk` 懒加载触发 `chunking.py:18`），Windows OpenMP 运行时冲突 → 确定性段错误。定位手段：faulthandler 抓崩溃栈（`logs/r2_probe_fh.log`）+ 7 组 A/B 探针（`logs/r2_probe*.log`、`scripts/r2_segfault_probe*.py`）。**修复**：`pipeline.py` 模块顶部预导入（提交 `8aafc75`），子进程回归守卫测试 `test_native_import_order.py`。

**R-P0-2 连带解决**：修复后启动恢复跑通，rag_test_kb 语料被增量同步完整索引——registry 从 3 active 变为 **24 active + 4 pending_review**，毒丸文档 `legal_采购合同.md` 正常入库（24 chunks，`chunk_id` 前缀 `de3cd3ebdc`）。

**全量 103 例离线基线（`LLM_MODEL=qwen3.7-plus python -m backend.evaluation rag --dataset rag_test_kb.json`，1m46s，exit=0）**：

| 指标 | 实测 | 任务书初版目标 | 判定 |
|---|---|---|---|
| 通过率 | 92.2%（95/103，8 失败 0 错误） | — | — |
| **Recall@5** | **0.8731** | ≥ 0.90 | ❌ 差 2.7pp，R3/R4 主攻点 |
| Recall@10 | 0.8731 | — | 与 @5 相同（候选截断一致） |
| MRR | 0.8277 | — | — |
| NDCG@10 | 0.8251 | — | — |
| Chunk 级召回 | 0.7767 | — | — |
| 语义 Chunk 召回@5 | 1.0000 | — | 嵌入侧质量好，瓶颈在融合/过滤 |
| **拒答准确率** | **1.0000**（negative 15/15） | ≥ 0.95 | ✅ |
| 分组 | smoke 100% / core 90.3% / hard 89.5% / regression 100% / ambiguous·calculation·process 各 80% | 必须分组展示 | ✅ 已按 query_type 分组 |
| RAGAS | 跳过（`No module named 'ragas'`） | — | 环境缺包，如实记录 |

**8 个失败用例**（详见 `data/eval_runs/2026-09-17T02-22-58-a2ef6b/eval-rag-20260917-022302.md` §8）：RC-026/039/041/090（采购合同金额/付款节点）、RC-051（物流SOP步骤）、RC-055（数据安全流程）、RC-080（歧义）、RC-082（退货）——5 例与 `legal_采购合同.md` 相关（该文档刚由恢复路径重建，指向切分/融合对 legal 类的适配问题），归因留给 R3/R4。

**测试回归**：定向套件 519 例 = 517 passed / 2 skipped / 0 failed（R0 基线 510+2 无退化 + 新增 7 例全过，`logs/r2_regression.log`）。注：日志末尾 exit=1 系 safe-delete 钩子对 pytest 临时目录要求批量确认所致（count>50），非测试失败，已用小批量运行（rc=0）交叉验证。

### 2.3 环境记录

Embedding：cloud dashscope `text-embedding-v3`（tracking=True）；向量库：本地 `data/chroma` + `data/doc_db`；registry/chunk_store：SQLite。

---

## 三、风险清单（检索质量专项视角，增量于 FMEA）

| 级别 | # | 风险 | 证据 | 建议归属 |
|---|---|---|---|---|
| **P0** | R-P0-1 | 评测 CLI 确定性段错误 + parsing 毒丸文档，基线评测完全不可用 | §2.2 | R2 前置 |
| **P0** | R-P0-2 | rag_test_kb 语料未索引（3 active），评测数据前提缺失 | §2.2 | R2 前置 |
| **P0** | R-P0-3 | 清洗原地覆盖，原文不可追溯，违反任务书 5.2 | pipeline.py:57-59 | R2 |
| **P1** | R-P1-1 | 版本治理缺失：无版本历史/有效期/supersedes，快照仅 financial | doc_registry.py:23-60; indexer.py:1240-1258 | R4 |
| **P1** | R-P1-2 | 评测标注按文件名，无 doc_id/chunk_id 级 ground truth | rag_test_kb.json | R3（标注格式设计时一并解决） |
| **P1** | R-P1-3 | 被过滤 chunk 无持久化明细 | indexer.py:598-602 | R3（质量门禁） |
| **P1** | R-P1-4 | registry SQLite 多进程并发写 + 无版本字段 | doc_registry.py:75 | C19（R1） |
| **P1** | R-P1-5 | BM25 全量 pickle 加载（2 万份第一瓶颈） | bm25_store.py（pickle corpus+docs） | R5 容量基线 |
| **P2** | R-P2-1 | chunk_id 位置派生，切分参数变更导致 id 全量漂移（含过时注释误导） | indexer.py:740 vs :514 | R3 设计 expected_chunk_ids 时决策 |
| **P2** | R-P2-2 | doc 级 16K 截断（长文档定位天然残缺，已打标） | indexer.py:47-49,828-830 | R4 |
| **P2** | R-P2-3 | 一致性对账全量 get() 扫描 | consistency.py:229-259 | R5 |
| **P2** | R-P2-4 | 章节 find() 映射兜底错配（已有切分侧权威注入兜底） | indexer.py:765-771 | 观察项 |
| **P2** | R-P2-5 | 元数据 LLM 失败静默降级默认值（有日志无打点） | indexer.py:646-647 | R3 质量门禁 |

## 四、R2 建议修复范围（按本清单收敛为 4 项）

1. R-P0-1 评测进程段错误定位与修复（先二分定位原生库冲突，恢复评测能力）；
2. R-P0-2 清理 parsing 卡住行 + 重建 rag_test_kb 索引 → 跑出 103 例真实基线（补记到本报告 §2.2）;
3. R-P0-3 AST 增加 raw_text 保留 + cleaning_operations 留痕（增量，不改清洗行为本身）;
4. R-P1-3 被过滤 chunk 明细持久化（入 quality report JSON 雏形）。

R-P1-1/P1-2 留给 R4/R3 按计划处理，不在 R2 扩量。
