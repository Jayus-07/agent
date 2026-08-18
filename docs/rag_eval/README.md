# RAG 评测模块

> 给 RAG 系统做"体检"，定位哪一段退化。

---

## 🚀 30 秒看懂

- **这是什么**：给你的 RAG 系统（6 段流水线 + 3 层 Gate）打分，告诉你"哪个环节最该改"
- **怎么跑**：每周手动跑一次，复制两条命令
- **在哪看**：`data/rag_eval/report.json` + 控制台摘要

---

## 1. 这是什么东西

**类比**：你的 RAG 系统是个学生，评测模块是"阅卷老师"。

```
旧评测（已有）              新评测（本模块）
─────────────────────────────────────────
只判"对不对"               还告诉你"为什么对/错"
   ↓                          ↓
pass / fail               6 段流水线分段打分
召回率                    + 3 层 Gate 分布
NDCG                      + Faithfulness Judge 准确性
```

**评测哪 5 个模块**（V1.5 范围）：

| # | 模块 | 评测什么 | 为什么不评测 |
|---|---|---|---|
| ③ | **Chunk Hybrid** | Stage 1 文档筛选 + Stage 2 chunk 检索 | 最关键 |
| ⑤ | **Rerank** | 重排序首位命中率 | 第二关键 |
| ⑥ | **LLM Generate** | Citation 引用准确性 | 影响可信度 |
| 🚪 | **Evidence Gate** | 3 层拒答分布 + Self-Correction 成功率 | 防误拒 |
| ⚖️ | **Faithfulness Judge** | Judge 准确性（MAE/F1） | 验证裁判 |

---

## 2. 为什么只做 5 个模块

之前的"完整版"有 6 个 Stage + Router + Ragas 扩库，**过度设计**。

V1.5 砍到 5 个模块的原因：

- **先跑通最关键的**：3 段流水线（③⑤⑥）+ 2 层评估（Gate/Judge）能定位 80% 的问题
- **不写 Router 评测**：影响小，留 V2.0
- **不做 Ragas 扩库**：扩库是"加 case"，不是"评测"本身
- **不做异源 Judge**：先用 DeepSeek 自评，V2.0 再切 qwen

> 完整 V2.0 路线图见 §6。

---

## 3. 5 分钟跑起来

### 前置

```bash
# 已有环境：Python 3.10+, 已安装 backend/requirements-dev.txt
cd backend
```

### 跑评测（3 个模块一次性）

```bash
# 1. 跑检索评测（已有，37 case）
PYTHONIOENCODING=utf-8 PYTHONPATH=".." \
  ../.venv/Scripts/python.exe -m evaluation rag --dataset rag_test_kb.json

# 2. 跑端到端评测（11 case，含 Gate/Citation）
PYTHONIOENCODING=utf-8 PYTHONPATH=".." \
  ../.venv/Scripts/python.exe -m evaluation e2e --live

# 3. 看报告
cat data/eval_runs/<最新 run_id>/report.json
```

### 看输出

```json
{
  "summaries": [{
    "module": "rag",
    "pass_rate": 1.0,
    "metrics": {
      "ndcg@10": 0.97,
      "rerank_top1_hit": 0.86,   ← 86%，有点低
      "hybrid_doc_recall": 0.95  ← 95%，很好
    }
  }]
}
```

---

## 4. 指标含义速查表

| 指标 | 含义 | 阈值 | 怎么算 |
|---|---|---|---|
| **pass_rate** | 用例通过率 | ≥95% | pass 数 / 总数 |
| **ndcg@10** | 前 10 结果排序质量 | ≥0.85 | 排序折损累积增益 |
| **hybrid_doc_recall** | Stage 1 文档召回率 | ≥0.85 | 召回对 / 应对 |
| **rerank_top1_hit** | 重排序首位命中 | ≥0.85 | top1 在 ground_truth |
| **llm_citation_accuracy** | 引用准确性 | ≥0.95 | [1][2] 对应真 chunk |
| **gate_false_reject_rate** | 误拒率 | ≤5% | 应答却被拒的比例 |
| **judge_score_mae** | Judge vs 人工 MAE | ≤0.15 | 评分误差 |
| **judge_unsupported_f1** | Judge 识假 F1 | ≥0.70 | unsupported 召回 F1 |

**看不懂某个指标？** 看 §7 FAQ。

---

## 5. 结果怎么看 / 怎么解读

### 一份典型报告

```
通过率=100%  (37/37)
  ├─ NDCG@10: 0.97 ✅  排序很好
  ├─ 召回率@10: 1.00 ✅  检索很全
  ├─ Rerank top1: 0.86 ⚠️  ← 这就是问题
  └─ Citation: 1.00 ✅  引用全对
```

### 怎么判断"该不该担心"

| 看到... | 说明 | 行动 |
|---|---|---|
| pass_rate < 95% | 有 case 答错 | 看 §5.1 失败明细 |
| rerank_top1 < 0.85 | 重排序有问题 | 调 Rerank 模型或阈值 |
| hybrid_doc_recall < 0.85 | 检索不够 | 调 embedding / chunk size |
| gate_false_reject > 5% | 误拒率高 | 调 Gate 阈值 |
| judge MAE > 0.20 | Judge 不准 | V2.0 切异源 Judge |

### 5.1 失败明细在哪看

```bash
# 单 case 详情（含 rerank_score、chunk_id、citation）
cat data/eval_runs/<run_id>/per_case/<case_id>.json
```

---

## 6. V1.5 没做的事

| 没做的事 | 原因 | 什么时候做 |
|---|---|---|
| HistoryAware / MultiQuery / AdaptiveExpansion 评测 | 影响小 | V2.0 |
| Self-Correction 延迟分析 | Gate 评测已覆盖 | V2.0 |
| QueryAnalyzer / LLM Router 评测 | 不是关键路径 | V2.0 |
| Ragas 自动扩库 | 需先验证 37 case 够不够 | 看 §7 FAQ |
| 异源 Judge（切 qwen2.5:3b） | V1.5 用 DeepSeek 自评 | V2.0 |

**为什么这些不做**：先跑通"基本功能"——能定位哪段退化，比"什么都能查"更重要。

---

## 7. 常见问题 FAQ

### Q1: 跑挂了怎么办？

```bash
# 看后端是否启动
cd backend && python -m app.main

# 看 trace 是否生成
ls data/rag_eval/  # 应该有 report.json
```

最常见错误：`ModuleNotFoundError: backend.evaluation` → 加 `PYTHONPATH=..`。

### Q2: 37 case 不够怎么办？

先扩到 50-100 条（从生产 badcase 录入）。**100+ 后再考虑** Ragas 自动扩库（V2.0）。

### Q3: 怎么加新 case？

`backend/evaluation/datasets/rag_test_kb.json` 加一条：
```json
{
  "id": "RT-038",
  "question": "新问题",
  "module": "rag",
  "kb_id": "rag_test_kb",
  "expected": {"relevant_docs": ["doc_id"], "relevant_snippets": ["关键词"]}
}
```

### Q4: Faithfulness Judge 用什么模型？

**当前用 DeepSeek-v4-flash**（同源评，有 bias）。V2.0 计划切到本地 qwen2.5:3b（异源）。

### Q5: 指标多久跑一次？

每周一次（手动）。如果你发现指标波动大 → 改每天一次。

### Q6: rerank_top1_hit = 0.86 该不该担心？

看你业务：客服类问题可以接受 0.80；合规/法律类问题应该 ≥0.90。

---

## 8. 代码在哪改

| 想改什么 | 看哪个文件 |
|---|---|
| 加新指标 / 改算法 | `docs/rag_eval/pipeline_eval.md` |
| 加新数据集 | `backend/evaluation/datasets/<module>.json` |
| 改跑分命令 | `backend/evaluation/cli.py` |
| 调 RAG 本身 | `backend/rag/` |

---

**配套文档**：
- `pipeline_eval.md` — 3 个 Stage 评测器代码（给开发者看）
- `RAG_DESIGN.md §6` — Faithfulness 设计（已 2026-08-15 更新）

**更新日期**: 2026-08-15
