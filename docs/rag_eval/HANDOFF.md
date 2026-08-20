# RAG 评测模块 — 会话交接文档

> 2026-08-19/20 RAG 评测深度迭代会话交接
>
> **给接手人**：这份文档是 24 小时迭代的完整记录，含设计决策、已验证方案、踩过的坑、当前基线。

---

## 0. TL;DR（30 秒看完）

- **RAG 评测通过率 90%（45/50）**，**真实 Top-1 准确率 58%**
- 100% 拒答准确率（8 条 negative case）
- 之前虚高 92% 通过率是**"recall 含期望 doc"**，掩盖了 Top-1 错的问题
- 评测框架 v2 改造完成：**Top-1 + 拒答指标 + 3 段式报告 + 失败分类**
- **唯一通过的优化**：同义词扩展检索（commit `2a9f629`），Top-1 56% → 58%
- **多个优化实验失败**：KB 扩展 / hybrid 权重 / rerank threshold / rerank 二次精排 / Stage1 doc_type boost / FAQ 合并切分 / 分场景阈值 — **全部边际收益为负**
- **当前 Top-1 58% 远低于 85% 生产门槛**，瓶颈在 rerank 模型本身（BGE-reranker-base 对同主题多文档区分不够），**不动模型只能到 60~65%**

---

## 1. 当前基线（50 条评测）

| 指标 | 值 | 含义 |
|---|---|---|
| **通过率 (recall@5)** | 90.0% (45/50) | Top-5 召回含期望 doc |
| **Top-1 准确率** | **58.0% (29/50)** | **用户看到的第1条是不是对的** |
| **拒答准确率** | 100.0% (8/8) | negative case 全拒答 |
| MRR | 85.0% | 第1命中位置倒数平均 |
| NDCG@10 | 86.83% | 排序质量 |
| recall@5 | 90.0% | Top-5 召回覆盖率 |
| Chunk 级召回率 | 74.0% | 召回 chunk 覆盖期望 chunk |

**生产上线标准**：Top-1 ≥ 85% — **当前差 27 个百分点**。

---

## 2. Commit 链（10 个，按时间顺序）

```
2a9f629  feat(rag): 同义词扩展检索 — Top-1 56% → 58% (+2%)
956dab4  feat(eval): HTML Dashboard 报告
b7eb8a6  feat(eval): Markdown 报告首屏加 Dashboard 块
4054099  docs(rag_eval): v2 评测集设计文档（10 章 + 3 附录，496 行）
3847d7c  feat(eval): v2 改造 — 3 段式报告 + Top-1/拒答指标 + negative case
0e80776  fix(eval): trace.end_span 错传 TraceRecord 导致部分 case 假性空召回
8f73f62  fix(rag): ChunkLevelRetriever metadata_filter 路径补 doc 级检索算 doc_ids
7f32825  fix(eval): golden truth 错标修正 (RT-004/006/028/029/030)
d1530f4  rag+eval: 删除废弃的 rag.json
7531f63  rag+eval: 上传链路加固 + 报告 trace 展开 + 编码修复
16fdad2  rag+eval: 同步 eval_upload_chain.py 自动追加评测 cases (RT-038+)
```

**stash 残留**：`stash@{0}` 保留 KB 扩展实验代码（4 处新增内容），**建议保留，未来某天可继续探索**

---

## 3. 核心设计原则（4 条不可违背）

### 3.1 Top-1 准确率 = 真实指标

```python
top1_hit = retrieved_docs[0] in expected_docs if retrieved_docs else False
metrics["top1_accuracy"] = 1.0 if top1_hit else 0.0
```

- 用户看到的第 1 条决定体验
- Top-5 通过但 Top-1 错，对用户没价值
- **不要看 recall@5 当成功指标**（92% 虚高 34%）

### 3.2 拒答准确率 = 安全指标

```python
if case.expected.get("should_reject"):
    # 期望拒答时，confidence in {none, low} 算拒答成功
    reject_accuracy = 1.0 if confidence in ("none", "low") else 0.0
```

- KB 缺答案时系统能否识别"我不知道"
- 比硬答错更重要

### 3.3 3 段式报告 = 过程透明

每条 case 渲染 3 段：
1. **过程细节** — pipeline + span 树 + Top-5 召回证据
2. **结果** — 期望 vs 实际 + Top-1 命中
3. **是否拒答** — confidence + gate + reason

### 3.4 negative case 覆盖盲区

8 条 negative case（RT-043~050）覆盖 7 类"应拒答"场景：
- 个人/敏感信息、未来/超范围、物理信息、内部技术栈、跨域、模糊 query、软性信息、个人信息

---

## 4. 已验证的失败方案（7 个 — 不要重复踩坑）

| # | 实验 | 改动 | 效果 | 根因 |
|---|---|---|---|---|
| 1 | KB 内容扩展 | 02_04_05_11_ 加新章节 | 通过率 92%→82% | 新内容稀释原文档关键词权重 |
| 2 | hybrid 权重 7:3 | vector 0.7 + BM25 0.3 | Top-1 -2% | vector 也会拉错（"包装盒"→商品包装） |
| 3 | rerank threshold 0.6 | 0.3 → 0.6 | recall -22% | chunks 实际 score 都在 0.5~0.6，过滤效果相同 |
| 4 | FAQ 合并切分（I1）| 相邻 2 个 Q/A 合并 | Top-1 -12% | chunk metadata 改变破坏 P0 文档召回 |
| 5 | 分场景 rerank 阈值（I2）| FAQ 0.5/Flow 0.3/Spec 0.4 | Top-1 -12% | 阈值+bonus 叠加过严 |
| 6 | rerank 二次精排（K2）| rerank sigmoid 后加 bonus 0.08 | recall -4% | bonus 把相关 chunks 推到 top-5 外 |
| 7 | Stage1 doc_type boost（K3）| Stage1 加 doc_type boost 0.1 | Top-1 -8% | 与 K1 叠加破坏 Stage1 召回平衡 |

**唯一通过**：K1 同义词扩展 — 改动是"召回扩展"（增加候选）而非"排序调整"，不破坏原有 sigmoid 阈值过滤。

**关键教训**：
- "加 bonus" 类调整几乎全部失败（叠加放大负效果）
- "召回扩展" 类调整成功（不改变排序，只增加候选）
- 真瓶颈在 **rerank 模型本身**，不在检索链路

---

## 5. 5 条真实 Top-1 错误（baseline 下的硬骨头）

| 用例 | 期望 doc | Top-1 | 根因 |
|---|---|---|---|
| **RT-006** | 02_售后FAQ (e4b6374ca7) | 01_FAQ (8bcf00f8b8) | 两个 FAQ 都含"保修"，rerank 区分不够 |
| **RT-014** | 05_商品规格 (83b7b646c6) | 06_退货政策 (a709fe1cf1) | "异味处理"语义被"商品损坏"拉走 |
| **RT-029** | 04_采购流程 (044f7fd36e) | 11_采购合同 (6ca78d7b4b) | 都是采购类 doc，rerank 选错 |
| **RT-032** | 11_采购合同 (6ca78d7b4b) | 01_FAQ (8bcf00f8b8) | 11_ 缺"保密"内容，FAQ 抢答 |
| RT-013（K1 已修）| 05_商品规格 | ~~Apple入库质检~~ → 05_商品规格 ✅ | K1 同义词扩展修复 |

---

## 6. 代码位置索引（重要文件）

| 文件 | 作用 | 改动历史 |
|---|---|---|
| `backend/evaluation/runners/builtin.py` | runner，confidence + top1 + reject 判定 | 0e80776 trace 修复 |
| `backend/evaluation/report.py` | Markdown + HTML 报告 3 段式渲染 | b7eb8a6 dashboard, 956dab4 HTML |
| `backend/evaluation/datasets/rag_test_kb.json` | 50 条评测用例 | 16fdad2 + 3847d7c |
| `backend/rag/retrieval/retrievers.py` | ChunkLevelRetriever 主链路 | 8f73f62 metadata_filter 修复 |
| `backend/rag/retrieval/hybrid.py` | RRF 融合（vector + BM25）| 2a9f629 expanded_queries |
| `backend/rag/reranker.py` | CrossEncoder rerank | 多次回滚，保持原版 |
| `backend/rag/base.py` | CustomRetriever（向量检索）| 2a9f629 expanded_queries |
| `backend/rag/preprocessing/synonyms.py` | **同义词字典（K1）** | 2a9f629 新建 |
| `backend/rag/indexing/indexer.py` | IncrementalIndexer | 7531f63 trace 展开 |
| `backend/rag/chain.py` | 端到端链路（含 4 个 EvidenceGate）| 已有拒答 |
| `docs/rag_eval/v2-evaluation-design.md` | **v2 设计文档（496 行）** | 4054099 |
| `docs/rag_eval/README.md` | 评测模块总览 | 已有 |

---

## 7. 踩过的坑（5 个）

### 7.1 Golden truth 错标

**症状**：12 条用例期望的 doc_id 是错的。
**修复**：commit `7f32825` 修 5 条（RT-004/006/028/029/030），每条加 `fix_note`。
**教训**：评测集本身需要评测 — 用 `parse_and_chunk()` + grep query 关键词来验证每个 `relevant_docs` 是否真的包含该信息。

### 7.2 trace.end_span 错传 TraceRecord

**症状**：12 条用例 `retrieved_docs=[]`（trace 状态污染导致 retriever.invoke 失败）。
**根因**：`builtin.py` line 269（修复前）调用 `trace_collector.end_span(trace)`，但 `end_span` 期望 `Span` 对象，TraceRecord 没有 `span_id`。
**修复**：commit `0e80776` 手动创建 root_span。
**教训**：评测 framework 必须能**自验证**（同一 query 在 debug 和 eval 下结果一致）。

### 7.3 KB 扩展实验失败

**实验**：给 02_/04_/05_/11_ 加新章节。
**结果**：通过率从 92% → 82%，Top-1 不变。
**根因**：新内容稀释原文档的关键词权重。
**教训**：加 KB 内容**不是解决检索问题的正确手段**，检索问题要改**检索算法**。

### 7.4 hybrid 权重实验失败

**实验**：vector 0.7 + BM25 0.3。
**结果**：Top-1 准确率 56% → 54%。
**根因**：vector 也不是越偏越好。
**教训**：vector vs BM25 是**取舍**，不是**单边强化**。

### 7.5 rerank threshold 收紧无效

**实验**：0.3 → 0.6。
**结果**：recall -22%，Top-1 -2%。
**根因**：chunks 实际 score 都在 0.5~0.6，0.5 和 0.6 几乎过滤相同内容。
**教训**：阈值不是瓶颈，真瓶颈是 rerank 模型本身。

---

## 8. 3 层 confidence 启发式判定

`backend/evaluation/runners/builtin.py` 中，runner 不调 LLM，用 rerank_score + gap 近似判定：

```python
if not details:
    confidence = "none"
elif top1_score < 0.5:
    confidence = "none"
elif top1_score < 0.6:
    confidence = "low"
elif score_gap < 0.15:
    confidence = "medium"
else:
    confidence = "high"
```

| 条件 | confidence | 含义 |
|---|---|---|
| 召回为空 | none | 无文档 |
| Top-1 < 0.5 | none | 检索没没把握 |
| 0.5 ≤ Top-1 < 0.6 | low | 不太确定 |
| Top-1 ≥ 0.6, gap < 0.15 | medium | 多文档平分 |
| Top-1 ≥ 0.6, gap ≥ 0.15 | high | 有把握 |

**注意**：这是离线启发式，**非真实 EvidenceGate**（真实 Gate 在 `chain.py` 端到端链路，含 LLM faithfulness 评估）。

---

## 9. 建议的下一步方向

### 9.1 短期（无模型升级，推荐）

**A. 同义词字典扩展**（30~50 组）：
- 当前 23 组，覆盖了历史失败 case 涉及的高频词
- 加 user feedback / query log 统计挖掘的同义词
- 风险：低（K1 路径已验证）
- 预期：Top-1 +2~5%

**B. 更安全的 doc_type 排序**：
- 不是 K3 那种"boost 不匹配的"
- 而是**"删除不匹配的 doc_type"**
- 例：FAQ 类 query 只保留 doc_type=faq 的 doc
- 风险：中（要确保 doc_type 检测准确）
- 预期：Top-1 +3~5%

### 9.2 中期（要 LLM 调用）

**C. MultiQuery always**：
- `MULTI_QUERY_MODE = "always"` 启用 LLM query 重写
- 本地 Ollama (qwen2.5:3b) 跑，每次 ~1~3 秒延迟
- 风险：中（LLM 引入新噪声）
- 预期：Top-1 +3~8%

### 9.3 长期（不动模型 = 不可能突破 70%）

**D. 接受现状**：
- Top-1 58% < 65% 内测门槛
- 当前 RAG 系统**不适合生产上线**
- 要突破必须升级 rerank 模型（BGE-reranker-large）— 用户机器负载大

---

## 10. 使用指南

### 10.1 跑评测

```bash
cd /d/Program\ Files/workplace/agent
python -m backend.evaluation rag --dataset rag_test_kb.json
```

### 10.2 看报告

- **HTML Dashboard**：`backend/evaluation/results/{ts}/eval-rag-{ts}.html`
- **Markdown 详情**：`.../eval-rag-{ts}.md`（3 段式）
- **JSON 全量**：`.../eval-rag-{ts}.json`

### 10.3 调 retriever 后回归测试

1. 改 retriever
2. 跑评测
3. 看 **Top-1 准确率**（不是 recall@5）
4. Top-1 涨 → 提交
5. Top-1 跌或不变 → 回滚

### 10.4 加新 case

- 选真实业务 query（不要编造）
- 验证 doc_id：grep query 关键词在 doc chunks 里
- 加 `metadata.probe_type` 标记
- 如果是负样本，加 `expected.should_reject=true`
- 跑评测确认通过

### 10.5 加新 KB 文档

**谨慎**！之前 KB 扩展实验已证明会破坏平衡。
- 验证其他 case 的 Top-1 不变
- 评测通过率不降
- 跑完整 50 条确认

---

## 11. 注意事项 / 后续接手陷阱

### 11.1 不要看 recall@5 当成功指标

90% recall@5 + 58% Top-1 准确率 = **虚假通过率 32%**。
生产上线标准是 Top-1 ≥ 85%。

### 11.2 不要同时改"阈值"+"bonus"

会叠加放大负效果（I2 / K2 都是这样失败的）。
**要只改一个**：要么改阈值，要么加 bonus。

### 11.3 doc_id 协议敏感

`doc_id = md5(filename.encode('utf-8')).hex_id[:10]`
改 KB 文件名 → doc_id 全失效 → 评测集 related_docs 全失效。
**不要改文件名**。

### 11.4 KB 文件不入仓

`.gitignore` line 57 排除 `backend/data/`。KB 文件运行时磁盘加载，跨机器部署需手动同步。

### 11.5 reset_rag_index 是破坏性操作

跑前必须确认：
1. working tree clean
2. 改动代码已 commit（stash 里的改动 reset 后不会丢）
3. 没有正在跑的 indexing 后台任务

### 11.6 K1 同义词字典扩展注意

扩展 query 时，**原 query rank_bonus=1.0**，**扩展 query rank_bonus=0.7**。
扩展过多会稀释原 query 信号，`max_expansions=4` 是合理上限。

---

## 12. 附录：评测集组成

| 类别 | 数量 | 占比 |
|---|---|---|
| FAQ 类 | 15 | 30% |
| 流程类 | 10 | 20% |
| 规格类 | 8 | 16% |
| 合同类 | 5 | 10% |
| 跨文档 | 4 | 8% |
| negative | 8 | 16% |
| **合计** | **50** | 100% |

难度分布：easy 18 / medium 20 / hard 12。

详细设计见 `docs/rag_eval/v2-evaluation-design.md`（10 章 + 3 附录，496 行）。

---

## 13. 快速参考

```bash
# 跑评测
python -m backend.evaluation rag --dataset rag_test_kb.json

# 清空索引（破坏性）
python backend/scripts/reset_rag_index.py

# 看最新报告
ls -t backend/evaluation/results/ | head -1

# 看 git 状态
git status --short

# 恢复 KB 扩展实验代码
git stash pop  # stash@{0}
```

---

**最后更新**：2026-08-20  
**会话负责人**：Claude (会话 c1a9)  
**接手建议**：先看 §0 TL;DR → §1 当前基线 → §9 下一步方向 → 决定要做哪个实验