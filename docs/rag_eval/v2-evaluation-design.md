# RAG 评测集设计文档 (v2)

> 2026-08-19 v2 改造：Top-1 准确率 + 拒答准确率 + 3 段式报告
>
> **TL;DR**: 92% 通过率不是真实能力，**真实 Top-1 准确率只有 56%**。本评测集让虚高 baseline 暴露出来。

---

## 一、设计动机

### 1.1 旧评测的盲区

v1 评测只报告 **recall@5/10 + MRR + NDCG**，即"前 N 个召回里有没有期望文档"。

**盲区**：
- 用户看到的 **Top-1** 是不是正确答案？评测集不知道
- KB 缺内容时，系统是否"敢拒答"？不知道
- 评测通过率高 → 生产环境真的能用？不知道

### 1.2 v1 实测的数字

`rag_test_kb.json` v1.1 37 条用例，跑出来 recall@5 = 92%。听起来不错。

但拆开看：
- 12 条 "假阳性"（trace bug 制造的空召回）
- 5 条 Top-1 错（如期望 04_采购流程，召回 11_采购合同）
- 5 条 Top-1 空（期望 doc 完全没召回到）
- 剩下 15 条是真的 recall@5 通过

**生产环境用户的真实体验 = Top-1 准确率 ≈ 56%**。

### 1.3 v2 的目标

让评测集揭示**用户实际体验到的准确率**，而不是虚高的召回率。

---

## 二、核心设计原则（4 条）

### 2.1 Top-1 准确率 = 真实指标

```python
top1_hit = retrieved_docs[0] in expected_docs if retrieved_docs else False
metrics["top1_accuracy"] = 1.0 if top1_hit else 0.0
```

用户看到的就是 Top-1。Top-5 通过但 Top-1 错，对用户没价值。

### 2.2 拒答准确率 = 安全指标

```python
should_reject = case.expected.get("should_reject", False)
if should_reject:
    # 期望拒答时，confidence in {none, low} 算拒答成功
    reject_accuracy = 1.0 if confidence in ("none", "low") else 0.0
else:
    reject_accuracy = None  # 正样本不输出
```

KB 缺答案时系统能否识别"我不知道"，比硬答错更重要。

### 2.3 3 段式报告 = 过程透明

每条 case 渲染 3 段：
1. **过程细节** — pipeline + span 树 + Top-5 召回证据
2. **结果** — 期望 vs 实际 + Top-1 命中
3. **是否拒答** — confidence + gate + reason

把"评测分数 → 失败原因"的链路打通。

### 2.4 negative case 覆盖盲区

8 条 negative case（RT-043~050）覆盖 7 类"应拒答"场景：
- 个人/敏感信息（CEO 薪酬）
- 未来/超范围信息（2027 年营收预测）
- 物理信息（办公室地址）
- 内部技术栈（React 版本）
- 跨域（苹果公司创始人）
- 模糊 query（"订单号怎么填"）
- 软性信息（企业文化）
- 个人信息（仓库主管是谁）

---

## 三、指标体系

### 3.1 当前指标（v2 实测）

| 指标 | 值 | 含义 |
|---|---|---|
| 通过率 (recall@5 pass) | 90.0% | Top-5 召回含期望 doc |
| **Top-1 准确率** | **56.0%** | **用户看到的第 1 条是不是对的** |
| **拒答准确率** | **100.0%** | 8 条 negative case 全拒答 |
| MRR | 83.0% | 第 1 命中位置倒数平均 |
| NDCG@10 | 84.83% | 排序质量（位置加权） |
| recall@5 / @10 | 90.0% | 召回覆盖率 |
| Chunk 级召回率 | 74.0% | 召回 chunk 覆盖期望 chunk 的比例 |

### 3.2 指标解释

**recall@5 vs Top-1 准确率的差距 = 34%**（92% → 58%），这部分是"召回有但排序错"的差距。

**Top-1 是产品决策的核心指标**：用户点开第一条是错的，就关掉了。

### 3.3 上线门槛建议

| 阶段 | Top-1 阈值 | 备注 |
|---|---|---|
| 内测 | ≥ 40% | MVP 可用 |
| 公测 | ≥ 65% | 用户体验可接受 |
| **生产** | **≥ 85%** | 商业级 |

**当前 56% = 内测都没过的水平**。

---

## 四、数据集组成（50 条）

### 4.1 用例分布

| 类别 | 数量 | 说明 |
|---|---|---|
| FAQ 类 | 15 | 商品/订单/物流常见问题 |
| 流程类 | 10 | 库存/采购/退货流程 |
| 规格类 | 8 | 商品规格/技术手册 |
| 合同类 | 5 | 采购合同/合规 |
| 跨文档/混合 | 4 | 涉及 2+ 文档 |
| negative | 8 | KB 无答案（应拒答） |
| **合计** | **50** | |

### 4.2 难度分布

| 难度 | 数量 | 比例 |
|---|---|---|
| easy | 18 | 36% |
| medium | 20 | 40% |
| hard | 12 | 24% |

### 4.3 用例字段

```json
{
  "id": "RT-038",
  "question": "跨境电商发到欧盟需要什么认证？",
  "module": "rag",
  "kb_id": "rag_test_kb",
  "expected": {
    "relevant_docs": ["fd2910f986"],
    "relevant_snippets": ["CE 认证", "HS"],
    "min_relevant_chunks": 1
  },
  "metadata": {
    "difficulty": "easy",
    "domain": "compliance",
    "probe_type": "ultra_short_doc",
    "probe_note": "...",
    "fix_note": "2026-08-19 修正 golden truth"
  }
}
```

字段说明：
- `relevant_docs`: 期望召回的 doc_id 列表（多 doc 表示跨文档）
- `relevant_snippets`: snippet 匹配关键词（match_type=snippet 时用）
- `should_reject`: true 表示期望系统拒答（仅 negative case）
- `match_type`: chunk_id（默认）/ snippet / doc_id
- `probe_type`: 用例探测目的（极短文档/标题党陷阱/同形异义/拒答 等）
- `fix_note`: 任何 golden truth 修正的历史记录

### 4.4 doc_id 协议

`doc_id = md5(filename.encode('utf-8')).hexdigest()[:10]`

例：
- `01_FAQ.md` → `8bcf00f8b8`
- `02_售后FAQ.docx` → `e4b6374ca7`
- `11_采购合同.md` → `6ca78d7b4b`

文件改名 → doc_id 变 → 评测集 related_docs 全失效。**不要轻易改文件名**。

---

## 五、3 段式报告

### 5.1 控制台摘要

```
  【RAG 检索】  通过率=90.0%  (45/50 通过, 5 失败, 0 错误)
    Top-1 准确率: 0.5600
    拒答准确率: 1.0000
    MRR: 0.8300
    NDCG@10: 0.8483
    召回率@5/10: 0.9000
    Chunk 级召回率: 0.7400
```

### 5.2 Markdown 报告 per-case

每个用例 3 段式：

```markdown
## ✗ RT-006 — 失败

**问题**: 如何申请商品保修？
**KB**: `rag_test_kb`
**指标**: Top-1 准确率=0.0, 召回率@5=1.0, MRR=0.5 ...
**总耗时**: 3200 ms

#### 1. 过程细节

**Pipeline 概览**:
| 阶段 | 值 |
| Stage1 召回数 | 5 |
| Stage1 fallback | — |
| Stage2 chunk 数 | 5 |
| Adaptive 决策 | 集中(1个文档) |

**关键 Span 树**:
| Span | 类型 | 状态 | 耗时(ms) | Metrics 摘要 |
| `检索` | retrieval | success | 50 | retrieved_chunks=8 ... |
| `Rerank` | rerank | success | 3050 | input=10, output=5 |

**召回证据 Top-5**:
| # | doc_id | chunk_id | rerank_score | snippet |
| 1 | `8bcf00f8b8` | xxx |0.62 | Q：商品保修期多久?... |
| 2 | `e4b6374ca7` | xxx | 0.58 | Q：维修申请 A：电子类... |
| ...

#### 2. 结果
- **期望文档**: `['e4b6374ca7']` (02_售后FAQ)
- **期望 snippets**: `['保修']`
- **Top-1**: `8bcf00f8b8`  ❌ 未命中
- **实际召回 Top-5**: `['8bcf00f8b8', 'e4b6374ca7', ...]`
- ❌ **未命中任何期望文档**

#### 3. 是否拒答
- **状态**: 🟢 **high**
- **Top-1 rerank_score**: `0.6210`
- 拒答判定: 不应拒答（正样本）
```

### 5.3 负样本（negative case）报告

```markdown
## ✓ RT-043 — 通过（正确拒答）

**问题**: 你们公司 CEO 的薪酬是多少？
...

#### 2. 结果
- **类型**: 负样本（应拒答）
- **期望文档**: `[]`（无答案）
- **实际召回**: `['7920f94bb3']`  （注：rerank_score=0.5095 触发 low confidence）
- ✅ **结果**: 召回非空但 confidence=low，触发 EvidenceGate 拒答

#### 3. 是否拒答
- **状态**: 🟠 **low**
- **Top-1 rerank_score**: `0.5095`
- **拒答判定**: ✅ 正确拒答 (reject_accuracy=1.0)
```

### 5.4 报告生成位置

- 单次跑：`backend/evaluation/results/{timestamp}/eval-rag-{timestamp}.md`
- 历史对比：`data/eval_runs/`（持久化）
- JSON 全量：`{same_path}/eval-rag-{timestamp}.json`

---

## 六、3 层 confidence 判定（启发式）

`backend/evaluation/runners/builtin.py` 中，runner 不调 LLM，用 rerank_score + gap 近似判定 confidence：

```python
if not details:
    confidence = "none"
    reject_gate = "retrieval"
    reject_reason = "no_evidence"
else:
    top1_score = details[0].get("rerank_score") or 0.0
    top2_score = details[1].get("rerank_score") or 0.0 if len(details) > 1 else 0.0
    score_gap = top1_score - top2_score
    if top1_score < 0.5:
        confidence = "none"
        reject_gate = "retrieval"
        reject_reason = "low_relevance"
    elif top1_score < 0.6:
        confidence = "low"
    elif score_gap < 0.15:
        confidence = "medium"
    else:
        confidence = "high"
```

**注意**：这是**离线启发式**，非真实 EvidenceGate。真实 Gate 在 `backend/rag/chain.py` 端到端链路，含 LLM 评估（faithfulness）和 self-correction。

阈值表：

| 条件 | confidence | 含义 |
|---|---|---|
| 召回为空 | none | 无文档 |
| Top-1 < 0.5 | none | 检索没把握 |
| 0.5 ≤ Top-1 < 0.6 | low | 不太确定 |
| Top-1 ≥ 0.6, gap < 0.15 | medium | 多文档平分 |
| Top-1 ≥ 0.6, gap ≥ 0.15 | high | 有把握 |

---

## 七、踩过的坑（5 个）

### 7.1 Golden truth 错标（v1.1）

**问题**：12 条用例期望的 doc_id 是错的。

**症状**：
- RT-028 期望 `aa3bad663c`（10_长文档），但"盘点/整改"在 `570819cca5`（03_库存管理）
- RT-029 期望 `aa3bad663c`，但"供应商/交期"在 `044f7fd36e`（04_采购流程）

**修复**：commit `7f32825` 修 5 条（RT-004/006/028/029/030），每条加 `fix_note`。

**教训**：评测集本身需要评测 — 用 `parse_and_chunk()` + grep query 关键词来验证每个 `relevant_docs` 是否真的包含该信息。

### 7.2 trace.end_span 错传 TraceRecord

**问题**：`builtin.py` line 269（修复前）调用 `trace_collector.end_span(trace)`，但 `end_span` 期望 `Span` 对象（带 `span_id`），TraceRecord 没有。

**症状**：12 条用例 `retrieved_docs=[]`（trace 状态污染导致 retriever.invoke 失败），但 retriever 单独跑能正常召回。

**修复**：commit `0e80776` 手动创建 root_span。

**教训**：
- 评测 framework 必须能**自验证**（同一 query 在 debug 和 eval 下结果一致）
- 如果 eval 报告空召回，先**单独跑 retriever** 验证真伪

### 7.3 KB 扩展实验失败（净效果 -10%）

**实验**：给 02_/04_/05_/11_ 加新章节（保修申请流程、交期管理、电水壶保养、保密义务）。

**结果**：通过率从 92% → 82%，Top-1 不变。

**根因**：新内容稀释原文档的关键词权重，让同 KB 内其他 doc 抢 Top-1 位置。

**教训**：
- 加 KB 内容**不是解决检索问题的正确手段**
- 检索问题要改**检索算法**，不是改**数据**
- 任何 KB 改动都要跑**完整评测**看真实指标变化

### 7.4 hybrid 权重 7:3 实验失败（净效果 -2%）

**实验**：vector_weight=0.7, bm25_weight=0.3（默认 0.5:0.5）。

**结果**：Top-1 准确率 56% → 54%。

**根因**：RT-040 query "Apple Watch **包装盒**" 被 vector 命中"包装盒"拉到 `83b7b646c6`（商品包装规范），而不是期望的 `7fae54d9ff`（Apple电子产品）。

**教训**：
- vector 也不是越偏越好
- BM25 字面匹配 vs vector 语义匹配是**取舍**，不是**单边强化**
- 单一权重调整影响所有 case，要看**整体 Top-1 准确率**而不是单个 case

### 7.5 rerank threshold 收紧无效果

**实验**：0.3 → 0.6。

**结果**：recall -22%，Top-1 -2%。

**根因**：chunks 的 rerank_score 实际都在 0.5~0.6 区间，0.5 和 0.6 几乎过滤相同内容。

**教训**：
- 阈值不是瓶颈
- 真瓶颈是 **rerank 模型本身**对同主题多文档的区分能力

---

## 八、当前未解决的问题

### 8.1 5 条真实 Top-1 错误

| 用例 | 期望 | Top-1 | 根因 |
|---|---|---|---|
| RT-006 | 02_售后FAQ | 01_FAQ | 两个 FAQ 同含"保修"，rerank 错 |
| RT-013 | 05_商品规格 | Apple入库质检 | "电水壶"主题多文档混淆 |
| RT-014 | 05_商品规格 | 06_退货政策 | "异味处理"语义被分散 |
| RT-029 | 04_采购流程 | 11_采购合同 | "采购"主题多文档混淆 |
| RT-032 | 11_采购合同 | 01_FAQ | 缺"保密"内容，被 FAQ 抢 |

### 8.2 已验证无效的方案

| 方案 | 效果 | 结论 |
|---|---|---|
| KB 内容扩展 | -10% | ❌ 破坏平衡 |
| hybrid 权重 7:3 | -2% | ❌ vector 也会拉错 |
| rerank threshold 0.6 | recall -22% | ❌ 不是瓶颈 |
| KB 加新测试文档 | 已加 16/24/25/26 入库 | ✓ 4/6 通过 |

### 8.3 待验证的方向

- **chunk 切分优化**：FAQ docx 不要按 Q/A 单切，2~3 个相邻 Q 合并
- **rerank 阈值分场景**：FAQ 类 query 用更严格阈值 0.5
- **rerank 模型升级**：`bge-reranker-base` → `bge-reranker-large`（有 GPU/显存需求）
- **MultiQuery 强制开启**：对复杂 query 做多角度检索

---

## 九、使用建议

### 9.1 日常使用

```bash
cd /d/Program\ Files/workplace/agent
python -m backend.evaluation rag --dataset rag_test_kb.json
```

### 9.2 看报告

打开 `backend/evaluation/results/{timestamp}/eval-rag-{timestamp}.md`，重点看：

1. **Top-1 准确率 + 拒答准确率**（核心指标）
2. **失败 case 的"3. 是否拒答"段**（confidence + gate + reason）
3. **失败 case 的"1. 过程细节"段**（span 树 + 召回证据找根因）

### 9.3 调 retriever 后的回归测试

1. 改 retriever（chunk_role 加权 / rerank 模型 / threshold）
2. 跑完整评测
3. 看 Top-1 准确率（**不是看 recall@5**）
4. 如果 Top-1 涨 → 提交
5. 如果 Top-1 跌或不变 → 回滚

### 9.4 加新 case

- 选真实业务 query（不要编造）
- 验证 doc_id：grep query 关键词在 doc chunks 里
- 加 `metadata.probe_type` 标记探测目的
- 如果是负样本，加 `expected.should_reject=true`
- 跑评测确认通过

### 9.5 加新 KB 文档

**谨慎**。先验证：
1. 文档被检索到不影响其他 case（看 Top-1 准确率不降）
2. 评测通过率 ≥ 90%

加完跑完整 50 条评测确认。

---

## 十、未来方向

### 10.1 短期（不改模型）

- [ ] chunk 切分策略优化（FAQ 合并切分）
- [ ] rerank 阈值分场景
- [ ] confidence 判定用 EvidenceGate 真值（chain.py 接入）

### 10.2 中期（改模型）

- [ ] rerank 模型升级（large 版本）
- [ ] MultiQuery 强制开启 + query rewrite
- [ ] embedding 微调（领域适应）

### 10.3 长期（架构）

- [ ] 评测集扩展到 200+ case（覆盖更多业务场景）
- [ ] 接入 e2e Faithfulness 评测
- [ ] 幻觉率指标（LLM-as-Judge）

---

## 附录 A：提交记录

| Commit | 内容 |
|---|---|
| `16fdad2` | 同步 eval_upload_chain.py 自动追加评测 cases |
| `7f32825` | golden truth 错标修正（RT-004/006/028/029/030）|
| `8f73f62` | ChunkLevelRetriever metadata_filter 路径补 doc 级检索 |
| `0e80776` | trace.end_span 错传 TraceRecord 修复 |
| `3847d7c` | v2 改造 — 3 段式报告 + Top-1/拒答指标 + negative case |

## 附录 B：相关代码位置

| 文件 | 作用 |
|---|---|
| `backend/evaluation/runners/builtin.py` | runner，confidence + top1 + reject 判定 |
| `backend/evaluation/report.py` | Markdown 报告 3 段式渲染 |
| `backend/evaluation/datasets/rag_test_kb.json` | 50 条评测用例 |
| `backend/rag/retrieval/retrievers.py` | ChunkLevelRetriever 主链路 |
| `backend/rag/retrieval/hybrid.py` | RRF 融合（vector + BM25）|
| `backend/rag/reranker.py` | CrossEncoder rerank |
| `backend/rag/chain.py` | 端到端链路（含 4 个 EvidenceGate）|

## 附录 C：参考资料

- RAGAS 框架设计
- LangChain Evaluation 文档
- LangGraph CRAG（Corrective RAG）拒答策略
- RAGFlow EvidenceGate 架构