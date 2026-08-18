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

---

## 9. 变更日志（2026-08-18）

### 9.1 报告增强：trace 过程数据完整呈现

旧 Markdown 只输出"指标表 + 失败/错误详情"，看不出"为什么 pass / 为什么排序靠后"。

新格式在 `## 过程详情（每条用例）` 章节展开：

| 子节 | 内容 |
|---|---|
| 标题 + 元信息 | 问题 / KB / 指标 / 总耗时 / 错误 |
| Pipeline 概览 | Stage1 召回数 / Stage2 chunk 数 / fallback 警告 / Adaptive 决策 |
| Trace Span 树 | 每条 span 的 type / status / duration_ms / metrics 摘要 |
| Span 详情 | 关键 span（retrieval / rerank / evidence_gate）的 input/output + events 流 |
| 召回证据 Top-5 | doc_id / chunk_id / rerank_score / snippet |
| 期望 vs 实际 | 期望文档、实际召回、命中 ✅ / 未命中 ❌ 判定 |

**为什么重要**：发现"召回对了但排序差" / "rerank 分数异常" / "KB 触发 fallback" 这类隐性故障，无需跑全量重做。

### 9.2 Qwen 接入通道

`backend/config/llm.py` 已注册 `qwen2.5:3b`（Ollama 本地）。配置入口：

```bash
# .env 设置 OLLAMA_BASE_URL（默认 http://localhost:11434）
# 切换模型
curl -X POST http://localhost:8000/llm/switch -d '{"model":"qwen2.5:3b"}'
```

**注意**：评测主调用链（Planner/Generator/Judge）默认仍走 DeepSeek（`LLM_MODEL=deepseek-v4-flash`），Qwen 接入通道备用。

### 9.3 RAG 上传链路加固

| 修复 | 内容 |
|---|---|
| **P0-1** | 清理 `tmpnl*_测评上传入库_*.md` GBK 乱码残留（已通过 `backend/scripts/cleanup_tmpnl_residuals.py` 永久清理） |
| **P0-2** | MIME 白名单收紧：客户端显式 `application/octet-stream` 必须拒绝（防扩展名伪装） |
| **P1-2** | 同文件并发上传加跨平台文件锁（Windows msvcrt / POSIX fcntl），避免同 doc_id 写两次到向量库 |
| **P1-3** | SSE 队列内存泄漏修复：`_run_index_background` 完成时主动 `pop`，即使客户端没订阅 SSE 也能清理 |
| **P1-4** | `ChunkingEmptyError` 新异常：扫描件 PDF / 纯图片 / 结构损坏 → SSE 推 error + 保留源文件供排查 |
| **P2-2 / P2-3** | `reindex_file` 不再反查 SQLite + 不再访问 `registry._lock/_conn()` 私有字段，改用 `bump_doc_version()` 公开方法 |
| **P1-5** | BM25 增量同步已实现（`bm25_store.add_documents`）— 之前的 review 误判为缺失 |

### 9.4 数据集变更

`backend/evaluation/datasets/rag.json` → `rag.v1.deprecated.json`（重命名 + 加 `_DEPRECATED` 标记）。原因是 `KD0001` 等虚拟 doc_id 与实际 doc_db UUID 协议不匹配。

当前默认加载顺序：`rag_v2.json`（policy_general KB 真实文档）优先 → 缺则回退 `rag.json`（已失效）。

### 9.5 日志编码修复

`backend/shared/logger.py`：

- console handler 强制 UTF-8（无论 `sys.stdout` 编码是什么），修复 Windows GBK 环境下中文乱码 + 静默丢失
- `ObservableLogger` 子类化替代 monkey-patch（不污染全局 `logging`）
- `handleError` 可观测：出错时把 record + traceback 写到 stderr，而不是默认吞掉

### 9.6 清理脚本

新增 `backend/scripts/cleanup_tmpnl_residuals.py`：幂等删除 GBK 残留 + ChromaDB + chunk_store + registry。
