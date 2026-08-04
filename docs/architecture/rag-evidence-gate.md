# RAG Evidence Gate — 主动拒答改造方案

> 最后更新: 2026-08-03
> 状态: 设计稿（待评审）
> 关联文档: [rag-system.md](rag-system.md) · [production-readiness.md](production-readiness.md)

---

## 〇、企业生产实践对标（CLAUDE.md L33 要求）

> 本章是后续所有设计决策的「事实基线」。任何与下表不一致的设计点需要在本章末尾给出「与企业实践对比」说明。

### 0.1 横向对比矩阵（5 类代表系统）

| 维度 | RAGFlow (开源) | Dify (开源) | LangGraph CRAG (工业参考) | Vertex AI Search (Google) | AWS Bedrock Guardrails (AWS) | 我方案现状 |
|------|---------------|-------------|--------------------------|--------------------------|----------------------------|----------|
| **Retrieval 阈值** | cosine_similarity ≥ **0.2** 单值 [1] | `score_threshold` 配置项（issue#7865 修过）[2] | **LLM grader**（structured output binary_score）[3] | relevanceScore ≥ 0.5（语义排序器输出） | similarity ≥ 0.5（chunk 级别拒答） | 计划引入 0.02 默认值 **（与企业差距明显）** |
| **Rerank 拒答机制** | 无独立 Rerank Gate，靠 similarity 一步到位 | Rerank 后的 top_k 整体相似度 | retrieval_grader 过滤 doc 后再 rerank | semantic_ranker 输出 0~1 | semantic_ranker + threshold | 计划 top1/avg/gap 三维 |
| **Generation 兜底** | Prompt 强约束「context 无 → 拒答」 | 同 RAGFlow，Prompt 约束 | LLM grader 输出 binary + 条件分流 | Groundedness 校验 + citation | 内容过滤 + citation | 计划改 JSON 强制输出（**与企业做法偏差**） |
| **Faithfulness 评估** | ❌ 无 | ❌ 无（仅 Ragas 集成） | ✅ LLM-as-judge `hallucination_check` | ✅ Groundedness score（默认 0.5 threshold）| ✅ Groundedness + Citation match | 已有 NLI（[guardrails/scorer.py](backend/rag/guardrails/scorer.py)）默认禁用 |
| **Citation 强制** | ❌（可选） | ❌（可选） | ✅ 引用支持度 grader | ✅ Citation match check | ✅ Citation Required | ✅ 已实现（[chain.py:482](backend/rag/chain.py#L482)） |
| **Self-Correction** | ❌ | ❌ | ✅ Rewrite → Re-retrieve | ❌（建议配 Agent） | ❌ | ❌ **（缺失）** |
| **输出层结构性校验** | ❌ | ❌ | ⚠️ grader | ✅ regex/结构校验 | ✅ regex 校验 | ❌ **（缺失）** |
| **拒答原因分类** | 1 类（no_relevant） | 1 类（低相似度） | grader 决策表（3-5 类） | groundedness 分数段 | 分类标签（unsafe/irrelevant 等） | 计划 8 类（**过细**） |
| **KB 反向驱动** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ 计划引入 |

### 0.2 企业共识（2025 主流做法汇总）

> 来源：[3][4][5] —— 三方独立报告一致收敛到以下三层防御栈：

```
┌─────────────────────────────────────────────────────────┐
│  Pre-generation（三选其一/多选）                          │
│  ✓ Retrieval Threshold（cosine ≥ 0.2 / Rerank ≥ 0.5）   │
│  ✓ LLM-based Retrieval Grader（binary_score）            │
│  ✓ Prompt 强约束："only use context, say I don't know"  │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Post-generation                                          │
│  ✓ Faithfulness / Groundedness（LLM-as-judge 主流）     │
│  ✓ Citation 强制（每 claim 一个引用）                     │
│  ✓ Claim-by-claim NLI（更细粒度）                        │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Output Layer                                             │
│  ✓ Regex / 结构校验（无引用即拒答）                      │
│  ✓ Token-level Grounding 检测（部分厂商）                │
└─────────────────────────────────────────────────────────┘
```

**关键洞察**:

1. **LLM-as-judge 评估主流** —— NLI 只在本地化部署场景用；云端 SaaS 全用 LLM judge（成本换精度）
2. **"I don't know" Prompt 兜底仍然必要** —— 即便有 faithfulness 校验，Prompt 仍是首要防线
3. **Citation 强制几乎全行业标配** —— 不仅"显示来源"，而是"未引用的 claim 直接拒"
4. **Self-correction 是 CRAG 的灵魂** —— 单轮 RAG 普遍被替代

### 0.3 与企业实践的偏差清单（修正依据）

> 后续章节所有设计点，先对照下表判别要保留/修正/删除：

| 方案点 | 企业做法 | 当前方案 | 偏差 | 处理建议 |
|-------|---------|---------|------|---------|
| Retrieval 阈值默认值 | RAGFlow 0.2 / Vertex 0.5 | 0.02 | ⚠️ 差 1 个数量级 | 改默认 **0.2**，与 RAGFlow 对齐；自己 KB 实测后微调 |
| 拒答评分维度 | LLM grader（柔性） / 1-2 个固定阈值 | top1/avg/gap 多维 | ➕ 比工业更严 | 保留但默认值放宽（top1=0.35 → 0.25）避免误拒 |
| Faithfulness 默认开关 | 主流默认 **开** | 默认 **关** | ❌ 落后 | 改 `ENABLE_FAITHFULNESS=true` 默认开 |
| Prompt 输出格式 | 主流仍是 Markdown + 强制 I don't know | 计划改 JSON | ❌ 偏离 | 改回 Markdown + 结尾 `<!--META-->` 元数据注释（与 Citation 兼容） |
| 拒答原因分类粒度 | 主流 3-5 类 | 计划 8 类 | ➖ 过细 | 精简为 5 类：`no_evidence / low_relevance / doc_type_mismatch / insufficient / hallucination` |
| KB Scope 白名单 | 主流 metadata_filter | 计划 OUT_OF_SCOPE | ➖ 过度 | 删除；用 doc_type 即可 |
| Self-Correction | CRAG 标配 | ❌ 缺 | ❌ 缺 | **新增**：拒答后 query rewrite 重试 1 次 |
| 输出层结构性校验 | 主流 regex | ❌ 缺 | ❌ 缺 | **新增**：LLM 输出无 `[1]` 引用时强制拒答 |
| 反向驱动（KB gap） | 主流无 | 计划 | ➕ 创新 | 保留，但告警频率按企业实践改 passive（每日报告）+ active（threshold 触发） |

### 0.4 即将对方案做的修正（基于 0.3 表）

按优先级：

**P0（与工业对齐，落地前必须）**：
1. 改 `QA_PROMPT`：回退 Markdown，结尾用 `<!--META-->` 注释带 `can_answer / citations`
2. 改 `ENABLE_FAITHFULNESS=true` 默认
3. 改 `VEC_MIN_SCORE` 默认 **0.2**（与 RAGFlow 对齐），先按 [config 注释] 标 "待校准"，D1 任务跑完采样后改
4. 精简 `RejectReason` 5 类
5. 删除 `OUT_OF_SCOPE` 与 KB Scope 白名单
6. 新增 Self-Correction：拒答后用 LLM 改写 query 重试 1 次（最多）

**P1（增强，建议补）**：
7. 输出层结构性校验：LLM 输出无任何 `[1]` → 触发拒答（与 Citation 联动）
8. `_verify_support` 阶段扩展：无引用即拒

**P2（创新保留）**：
9. 反向驱动 `evidence_gap_analyzer.py` 保留
10. 多维分数（top1/avg/gap）保留，但放宽默认值

---

## 一、问题陈述

### 1.1 当前 RAG 的"幻觉风险窗口"

代码现状 ([backend/rag/chain.py:60-76](backend/rag/chain.py#L60-L76) + [backend/rag/reranker.py:33-96](backend/rag/reranker.py#L33-L96)):

```
HistoryAware → MultiQuery → ChunkLevel Hybrid → Adaptive 扩展 → Rerank → LLM Generate
                                                              ↑
                                                仅靠 RERANK_SCORE_THRESHOLD=0.3
                                                单条阈值防线，无多维证据评估
```

具体失效模式:

| 模式 | 触发条件 | 当前行为 | 风险等级 |
|------|---------|---------|---------|
| **空召回仍答** | ChromaDB 返回 [] 或 [兜底低分chunk] | LLM 拿空 context 自由发挥 | P0（生产已发生） |
| **弱相关强答** | top3 rerank_score ∈ [0.3, 0.5] 都被保留 | LLM 利用弱关联推理补全 | P0 |
| **相关但跨域** | 命中"sop"但用户问"合规" | LLM 当 SOP 答合规问题 | P1 |
| **拒绝格式不稳定** | 检索失败、context 不足 | Prompt 只说"资料未提及"，未固定 JSON 结构 | P1 |
| **拒答原因零追溯** | 任意层级拒答 | Trace 无 reject.reason 字段，无法分析知识缺口 | P1 |

### 1.2 改造目标

将 RAG 决策逻辑从「**有检索结果就回答**」升级为「**只有证据充分才回答**」，并在以下四层建立强制证据评估:

1. **Retrieval** — 空召回/全低分时直接拒答，不进 Rerank
2. **Rerank** — 多维分数阈值（top1/avg/gap），低于门槛拒答
3. **Generation** — Prompt 强约束，格式稳定的拒答 JSON
4. **Evaluation** — 后置 NLI 验证，幻觉 claim 二次拒答或剔除

---

## 二、整体流程图

```
                 ┌────────────────────────────────────────┐
                 │           User Question                 │
                 └────────────────┬───────────────────────┘
                                  │
        ┌─────────────────────────┴─────────────────────────┐
        │ ① QueryAnalyzer (现有)                            │
        │    doc_type / domain / intent / entities           │
        └─────────────────────────┬─────────────────────────┘
                                  │ ParsedQuery
                                  ▼
        ┌──────────────────────────────────────────────┐
        │ ② Hybrid Retrieval (现有: vector + BM25)     │
        │    → RRF 融合 → top 8                       │
        └─────────────────────────┬──────────────────────┘
                                  │
                                  ▼
        ╔═══════════════════════════════════════════════╗
        ║ ③ EVIDENCE GATE #1: Retrieval Gate (新增)    ║
        ║   check:                                       ║
        ║     - docs 非空                                ║
        ║     - top1 vector_score ≥ VEC_MIN              ║
        ║     - 命中 doc_type 覆盖意图                   ║
        ║   失败 → RETRIEVAL_REJECT                      ║
        ╚═══════════════════════════════════════════════╝
                                  │ pass
                                  ▼
        ┌──────────────────────────────────────────────┐
        │ ④ Adaptive + Rerank (现有)                   │
        │    CrossEncoder 精排                          │
        └─────────────────────────┬──────────────────────┘
                                  │ scored docs
                                  ▼
        ╔═══════════════════════════════════════════════╗
        ║ ⑤ EVIDENCE GATE #2: Rerank Gate (新增)       ║
        ║   check:                                       ║
        ║     - top1_score ≥ RERANK_MIN_TOP1            ║
        ║     - topK_avg ≥ RERANK_MIN_AVG               ║
        ║     - (top1 - topK_min) ≥ RERANK_GAP          ║
        ║     - 高风险 query 额外要求 top2 同源         ║
        ║   失败 → RERANK_REJECT                         ║
        ╚═══════════════════════════════════════════════╝
                                  │ pass
                                  ▼
        ┌──────────────────────────────────────────────┐
        │ ⑥ LLM Generate (Prompt 增强 + 拒答 JSON)    │
        │    context 不足 → 固定格式输出                │
        └─────────────────────────┬──────────────────────┘
                                  │
                                  ▼
        ╔═══════════════════════════════════════════════╗
        ║ ⑦ EVIDENCE GATE #3: Faithfulness (现有启用)  ║
        ║   NLI 验证 claim，若整段 unsupported →       ║
        ║   HALLUCINATION_REJECT / 局部剔除              ║
        ╚═══════════════════════════════════════════════╝
                                  │
                                  ▼
        ┌──────────────────────────────────────────────┐
        │ ⑧ 最终回答（含 reject.reason/null）          │
        └──────────────────────────────────────────────┘
```

---

## 三、四层 Evidence Gate 设计

### 3.1 Retrieval Gate（新增）

**位置**: `backend/rag/retrieval/hybrid.py` `hybrid_retrieve()` 出口 → 新增 [backend/rag/evidence_gate.py](backend/rag/evidence_gate.py) `evidence_gate_retrieval()`

**当前现状**: `hybrid_retrieve()` 不判断"是否有意义的结果"，只机械返回 top-k。

**改造代码骨架**:

```python
# backend/rag/evidence_gate.py  (新建)
"""Evidence Gate — Retrieval/Rerank/Faithfulness 三层拒答判定"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List

from backend.config import (
    VEC_MIN_SCORE, DOC_TYPE_COVERAGE_REQUIRED,
)
from backend.shared.logger import logger

class RejectReason(str, Enum):
    NO_RETRIEVAL = "no_retrieval"                  # 空召回
    LOW_SIMILARITY = "low_similarity"              # 所有 chunk 分数过低
    DOC_TYPE_MISMATCH = "doc_type_mismatch"        # 召回类型与意图不符
    RERANK_FAILED = "rerank_failed"                # top1/avg/gap 不达标
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # 多维分数综合判定
    HALLUCINATION_CHECK_FAILED = "hallucination_check_failed"  # NLI 拒绝
    OUT_OF_SCOPE = "out_of_scope"                  # 超出业务范围
    LLM_GENERATED_REJECT = "llm_generated_reject"  # 模型主动输出拒答

@dataclass
class GateDecision:
    passed: bool
    reason: RejectReason | None
    score: float = 0.0
    diagnostics: dict = field(default_factory=dict)
    """供 Trace 展示的诊断信息"""


def evidence_gate_retrieval(docs: list, query_analysis) -> GateDecision:
    """Retrieval Gate: 接入 hybrid_retrieve() 出口之后、Rerank 之前。"""
    if not docs:
        return GateDecision(False, RejectReason.NO_RETRIEVAL,
                            diagnostics={"hint": "hybrid_retrieve 返回空列表"})

    # top1 向量分（rerank 后才更新，此处用 hybrid 暴露的 RRF 分）
    top_score = max(
        (d.metadata.get("rrf_score", 0.0) for d in docs), default=0.0
    )
    if top_score < VEC_MIN_SCORE:  # 默认 0.02 经验值
        return GateDecision(False, RejectReason.LOW_SIMILARITY,
                            score=top_score,
                            diagnostics={"top_score": top_score,
                                         "threshold": VEC_MIN_SCORE,
                                         "top3": [_doc_preview(d) for d in docs[:3]]})

    # doc_type 覆盖检查：意图命中 policy/compliance 时必须召回同类型 chunk
    if DOC_TYPE_COVERAGE_REQUIRED and query_analysis.doc_types:
        expected = set(query_analysis.doc_types)
        actual = {d.metadata.get("doc_type", "") for d in docs if d.metadata.get("doc_type")}
        if expected and not (actual & expected):
            return GateDecision(False, RejectReason.DOC_TYPE_MISMATCH,
                                diagnostics={"expected_types": list(expected),
                                             "actual_types": list(actual)})

    return GateDecision(True, None, score=top_score,
                        diagnostics={"top_score": top_score, "doc_count": len(docs)})
```

**配置项**（新增到 `backend/config/rag.py`）:

```python
# Evidence Gate #1: Retrieval
VEC_MIN_SCORE = float(os.getenv("VEC_MIN_SCORE", "0.02"))
DOC_TYPE_COVERAGE_REQUIRED = os.getenv("DOC_TYPE_COVERAGE_REQUIRED", "true").lower() == "true"
```

**判断要点**:
- **top-k 有结果但全弱相关**: 用 `VEC_MIN_SCORE` 兜底，top1 都没过线就拒答
- **query rewrite 后再判断**: 已通过 MultiQuery 解决（[multi_query.py:70-82](backend/rag/retrieval/multi_query.py#L70-L82)），但 **rewrite 仍失败需要在 Trace 里标注 fallback_used**，避免沉默失败
- **业务范围外**: 由 QueryAnalyzer 的 intent 决定 — 如果 `classify_intent` 返回未知域（未来扩展 KB Scope 白名单），直接 OUT_OF_SCOPE

### 3.2 Rerank Gate（新增）

**位置**: [backend/rag/reranker.py:33](backend/rag/reranker.py#L33) `rerank()` 返回前插入 `evidence_gate_rerank()`

**当前现状**: 仅一条 `RERANK_SCORE_THRESHOLD=0.3`（config:55）做阈值过滤，无法区分"全都刚好过线"vs"top1 远超其他"。

**改造代码骨架**:

```python
# backend/rag/evidence_gate.py  (续)

def evidence_gate_rerank(
    scored_docs: list[tuple],  # [(doc, rerank_score), ...]
    *,
    intent: str = "summary_query",
    risk_level: str = "low",
) -> GateDecision:
    """Rerank Gate: 多维分数综合判定。"""
    if not scored_docs:
        return GateDecision(False, RejectReason.RERANK_FAILED,
                            diagnostics={"hint": "rerank 返回空（threshold 过滤后）"})

    from backend.config import (
        RERANK_MIN_TOP1, RERANK_MIN_AVG,
        RERANK_MIN_GAP, RERANK_HIGH_RISK_MIN_TOP1,
        RISKY_INTENTS,
    )

    scores = [float(s) for _, s in scored_docs]
    top1 = scores[0]
    avg = sum(scores) / len(scores)
    gap = top1 - scores[-1] if len(scores) > 1 else 0.0

    # 风险等级提升门槛（policy/compliance/legal/intent="fact_query"）
    is_risky = intent in RISKY_INTENTS or risk_level != "low"
    min_top1 = RERANK_HIGH_RISK_MIN_TOP1 if is_risky else RERANK_MIN_TOP1

    metrics = {"top1": top1, "avg": round(avg, 4), "gap": round(gap, 4),
               "intent": intent, "risk_level": risk_level}

    if top1 < min_top1:
        return GateDecision(False, RejectReason.RERANK_FAILED, score=top1,
                            diagnostics={**metrics, "failed_rule": "top1",
                                         "threshold": min_top1})

    if avg < RERANK_MIN_AVG:
        return GateDecision(False, RejectReason.INSUFFICIENT_EVIDENCE, score=avg,
                            diagnostics={**metrics, "failed_rule": "avg",
                                         "threshold": RERANK_MIN_AVG})

    # 高风险问题：top1 与 top2 必须是不同 chunk（同源不充分）
    if is_risky and len(scores) >= 2:
        if (scored_docs[0][0].metadata.get("chunk_id") ==
            scored_docs[1][0].metadata.get("chunk_id")):
            return GateDecision(False, RejectReason.INSUFFICIENT_EVIDENCE,
                                diagnostics={**metrics,
                                             "failed_rule": "duplicate_top_chunks",
                                             "top1_id": scored_docs[0][0].metadata.get("chunk_id"),
                                             "top2_id": scored_docs[1][0].metadata.get("chunk_id")})

    return GateDecision(True, None, score=top1, diagnostics=metrics)
```

**配置项**（新增到 `backend/config/rag.py`）:

```python
# Evidence Gate #2: Rerank
RERANK_MIN_TOP1 = float(os.getenv("RERANK_MIN_TOP1", "0.35"))
RERANK_MIN_AVG = float(os.getenv("RERANK_MIN_AVG", "0.30"))
RERANK_MIN_GAP = float(os.getenv("RERANK_MIN_GAP", "0.05"))
RERANK_HIGH_RISK_MIN_TOP1 = float(os.getenv("RERANK_HIGH_RISK_MIN_TOP1", "0.55"))

RISKY_INTENTS = {"policy_query", "compliance_query", "fact_query"}
"""需要更高证据门槛的意图分类（与 classify_intent() 对齐）"""
```

**判断要点**:
- **top1 阈值**: 经验值 0.35（CrossEncoder sigmoid 输出范围 [0,1]）
- **topK 平均分**: 防止"top1 很高但只有它一个"的情况
- **分数差距**: 防止"全部 0.31 ~ 0.32" 这种"全刚过线"的伪相关
- **高风险问题额外要求**: top1 和 top2 来自不同 chunk，避免单点证据
- **避免"相关但不能回答"**: top1 ≥ 0.55 但 doc_type 是 training/faq（不是 policy）时，可降级要求 doc_type 复核（与 Retrieval Gate 联合）

### 3.3 Generation Gate（改造现有 Prompt + 拒答 JSON）

**位置**: [backend/rag/chain.py:60-76](backend/rag/chain.py#L60-L76) `QA_PROMPT` + `RAGChain.ask()` LLM 输出解析

**当前现状**: QA_PROMPT 已说"资料未提及"，但格式不固定，LLM 可能自由发挥。

**改造方案**:

```python
# backend/rag/chain.py 改造 QA_PROMPT

REJECTION_FORMATS = {
    RejectReason.NO_RETRIEVAL: "知识库暂无相关资料。",
    RejectReason.LOW_SIMILARITY: "知识库中未找到与问题强相关的内容，建议调整提问关键词或扩大检索范围。",
    RejectReason.DOC_TYPE_MISMATCH: "知识库中检索到的内容类型与问题不匹配（需要：{expected}，检索到：{actual}）。",
    RejectReason.RERANK_FAILED: "知识库可能存在相关内容，但当前召回的证据不足以可靠回答。",
    RejectReason.INSUFFICIENT_EVIDENCE: "召回证据不充分，多条支撑内容相关性较弱。",
    RejectReason.HALLUCINATION_CHECK_FAILED: "已生成的答案中包含未经资料支撑的事实，已自动剔除。",
    RejectReason.OUT_OF_SCOPE: "该问题超出当前知识库覆盖范围。",
}

QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是跨境电商知识库助手。你**只能**根据下方提供的资料回答问题。

【强制规则】
1. 每个事实/数据必须标注来源编号 [1][2][3]，禁止无引用声明
2. **资料未提及** → 只输出 JSON: {"answer":"<简短说明>","can_answer":false,"reason":"<下方预设原因之一>"}
   - 允许的 reason 取值：
     no_relevant_document | low_similarity | insufficient_evidence | out_of_scope
3. **可以回答** → 只输出 JSON: {"answer":"<正文>","can_answer":true,"citations":[1,2]}
4. 禁止编造、禁止使用资料外的知识
5. 资料中的数字、日期、名称必须与原文一致

【拒答示例】
资料: [文档1] 来源: 退货政策.md\n日期: 2024-03-01

问题: 今年的营收目标是多少？

输出:
{"answer":"知识库未提供营收目标相关资料。","can_answer":false,"reason":"no_relevant_document"}

资料: {context}"""),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])
```

**代码侧解析 LLM JSON**:

```python
# backend/rag/evidence_gate.py 新增

def parse_llm_output(raw: str) -> tuple[str, bool, RejectReason | None]:
    """解析 LLM 的 JSON 输出。失败时 fallback 到原始 markdown。"""
    import json, re
    # 尝试提取 JSON 块
    m = re.search(r'\{.*?\}', raw, re.DOTALL)
    if not m:
        return raw, True, None  # 无法解析 → 视作回答（保守）
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return raw, True, None

    can_answer = obj.get("can_answer", True)
    if not can_answer:
        reason_str = obj.get("reason", "no_relevant_document")
        try:
            reason = RejectReason(reason_str)
        except ValueError:
            reason = RejectReason.NO_RETRIEVAL
        return obj.get("answer", "知识库未提供此问题相关信息。"), False, reason
    return obj.get("answer", raw), True, None
```

### 3.4 Evaluation Gate（启用并升级现有 Faithfulness）

**位置**: [backend/rag/chain.py:359-399](backend/rag/chain.py#L359-L399) `_evaluate()` + [backend/rag/guardrails/scorer.py](backend/rag/guardrails/scorer.py)

**当前现状**:
- `ENABLE_FAITHFULNESS=false`（默认关闭）
- 仅三级漏斗（pass/mark/cite/rewrite），无整体拒答

**改造方案**:

```python
# backend/rag/chain.py _evaluate() 改造

def _evaluate(self, answer: str, context_docs: list) -> str:
    # ... 现有 Faithfulness 检测 ...
    result = self._last_faithfulness

    # 新增：整体拒答条件
    from backend.config import (
        FAITHFULNESS_REJECT_SCORE, HIGH_RISK_REJECT_SCORE,
    )
    intent = self._last_intent or "summary_query"  # 由 _execute 注入
    reject_threshold = (
        HIGH_RISK_REJECT_SCORE if intent in {"policy_query", "compliance_query"}
        else FAITHFULNESS_REJECT_SCORE
    )

    if result and result.enabled and result.score < reject_threshold:
        # 整段拒答
        reason = RejectReason.HALLUCINATION_CHECK_FAILED
        reject_msg = REJECTION_FORMATS[reason]
        self._last_reject_reason = reason
        logger.warning(
            f"[RAGChain] Faithfulness {result.score:.2f} < {reject_threshold} → 整体拒答"
        )
        return reject_msg  # 不返回清洗后答案，整个拒掉

    # 原三级漏斗逻辑（局部 mark/cite/rewrite）
    if result.cleaned_answer and result.cleaned_answer != answer:
        return result.cleaned_answer
    return answer
```

**配置项**:

```python
# backend/config/rag.py
ENABLE_FAITHFULNESS = os.getenv("ENABLE_FAITHFULNESS", "true").lower() == "true"  # 默认开
FAITHFULNESS_REJECT_SCORE = float(os.getenv("FAITHFULNESS_REJECT_SCORE", "0.5"))
HIGH_RISK_REJECT_SCORE = float(os.getenv("HIGH_RISK_REJECT_SCORE", "0.7"))
```

---

## 四、Reject 原因结构

### 4.1 结构定义

```python
# backend/rag/evidence_gate.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time

class RejectReason(str, Enum):
    """拒答原因枚举 — Trace / 前端 / 反向优化 统一来源。"""
    # Retrieval Gate
    NO_RETRIEVAL = "no_retrieval"
    LOW_SIMILARITY = "low_similarity"
    DOC_TYPE_MISMATCH = "doc_type_mismatch"
    OUT_OF_SCOPE = "out_of_scope"
    # Rerank Gate
    RERANK_FAILED = "rerank_failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    # Evaluation Gate
    HALLUCINATION_CHECK_FAILED = "hallucination_check_failed"
    # Generation Gate
    LLM_GENERATED_REJECT = "llm_generated_reject"


REJECT_LAYER = {
    RejectReason.NO_RETRIEVAL:         "retrieval",
    RejectReason.LOW_SIMILARITY:       "retrieval",
    RejectReason.DOC_TYPE_MISMATCH:    "retrieval",
    RejectReason.OUT_OF_SCOPE:         "retrieval",
    RejectReason.RERANK_FAILED:        "rerank",
    RejectReason.INSUFFICIENT_EVIDENCE:"rerank",
    RejectReason.HALLUCINATION_CHECK_FAILED: "evaluation",
    RejectReason.LLM_GENERATED_REJECT: "generation",
}


@dataclass
class RejectInfo:
    """嵌入到 TraceRecord.metadata.rejection 的拒答诊断信息。"""
    rejected: bool = False
    layer: str = ""                          # retrieval | rerank | generation | evaluation
    reason: Optional[RejectReason] = None
    fallback_action: str = ""                # "fixed_response" | "use_cleaned_answer" | "human_handoff"
    scores: dict = field(default_factory=dict)  # {"top1": 0.42, "avg": 0.31, ...}
    thresholds: dict = field(default_factory=dict)  # 触发的判定阈值
    suggested_queries: list = field(default_factory=list)  # 给用户的可选改写建议
    knowledge_gap_signal: dict = field(default_factory=dict)  # 反向优化用
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "rejected": self.rejected,
            "layer": self.layer,
            "reason": self.reason.value if self.reason else None,
            "fallback_action": self.fallback_action,
            "scores": self.scores,
            "thresholds": self.thresholds,
            "suggested_queries": self.suggested_queries,
            "knowledge_gap_signal": self.knowledge_gap_signal,
            "timestamp": self.timestamp,
        }
```

### 4.2 反向驱动知识库 — `knowledge_gap_signal`

当 `rejected=True` 时，构造 gap signal:

```python
def build_gap_signal(reject_info: RejectInfo, question: str, query_analysis) -> dict:
    return {
        "question_hash": hashlib.md5(question.encode()).hexdigest()[:12],
        "question": question,
        "intent": query_analysis.intent,
        "expected_doc_types": query_analysis.doc_types,
        "expected_business_domain": query_analysis.domains,
        "extracted_entities": query_analysis.persons + query_analysis.sku_codes,
        "reject_layer": reject_info.layer,
        "reject_reason": reject_info.reason.value if reject_info.reason else None,
        "top_retrieved_doc_types": reject_info.scores.get("top3_doc_types", []),
        "top_retrieved_sources": reject_info.scores.get("top3_sources", []),
        "occurrence_count": 1,  # 由聚合任务累计
        "first_seen": reject_info.timestamp,
    }
```

---

## 五、Trace 页面改造

### 5.1 TraceRecord 新增字段

[backend/rag/tracer.py:110-134](backend/rag/tracer.py#L110-L134) `TraceRecord` 增加:

```python
@dataclass
class TraceRecord:
    # ... 现有字段 ...
    rejection: RejectInfo = field(default_factory=RejectInfo)
    knowledge_gaps: list = field(default_factory=list)  # 反向优化用
```

### 5.2 Span 增加 `evidence_gate` 类型

[backend/rag/tracer.py:47-83](backend/rag/tracer.py#L47-L83) `SpanKind` 枚举增加:

```python
class SpanKind(str, Enum):
    # ... 现有 ...
    RETRIEVAL_GATE = "retrieval_gate"
    RERANK_GATE = "rerank_gate"
    FAITHFULNESS_GATE = "faithfulness_gate"
```

[backend/rag/chain.py:255-285](backend/rag/chain.py#L255-L285) `_execute()` 中插入三个 Gate span:

```python
def _execute(self, question, chat_history):
    from backend.rag.tracer import trace_collector
    from backend.rag.evidence_gate import (
        evidence_gate_retrieval, evidence_gate_rerank, parse_llm_output,
    )

    # 现有 retrieval span ...
    result = self.chain.invoke({"input": question, "chat_history": chat_history})

    # === Evidence Gate #1: Retrieval ===
    ret_gate_span = trace_collector.start_span(
        "evidence_gate_retrieval", name="证据门-检索",
        kind=SpanKind.RETRIEVAL_GATE.value,
    )
    ret_decision = evidence_gate_retrieval(
        result.get("context", []), self._last_query_analysis
    )
    trace_collector.end_span(ret_gate_span,
        metrics=ret_decision.diagnostics,
        status="success" if ret_decision.passed else "rejected")

    if not ret_decision.passed:
        return self._build_rejection_response(ret_decision)

    # === Evidence Gate #2: Rerank（在 _timed_stuff 之前，rerank span 已有） ===
    # 注: 已在 reranker.py 内部判定
    rerank_docs = [(d, d.metadata.get("rerank_score", 0.0))
                   for d in result.get("context", [])]
    rerank_decision = evidence_gate_rerank(
        rerank_docs, intent=self._last_intent,
        risk_level=self._risk_level,
    )
    # ... record & 可能拒答 ...

    return result
```

### 5.3 前端展示字段（伪 DTO 给前端参考）

```typescript
// frontend/src/types/trace.ts 扩展
interface TraceRecord {
  // ... 现有字段 ...
  rejection?: {
    rejected: boolean;
    layer: 'retrieval' | 'rerank' | 'generation' | 'evaluation' | null;
    reason: string | null;          // RejectReason enum value
    fallback_action: string;
    scores: Record<string, number>;
    thresholds: Record<string, number>;
    suggested_queries: string[];
    knowledge_gap_signal: {
      intent: string;
      expected_doc_types: string[];
      top_retrieved_doc_types: string[];
      question_hash: string;
    };
    timestamp: string;
  };
  knowledge_gaps?: KnowledgeGap[];  // 关联的反向优化任务
}
```

Trace 详情页（[frontend/src/app/observability/traces/[id]/page.tsx](frontend/src/app/observability/traces/%5Bid%5D/page.tsx)）改造:

1. **拒绝横幅**: 当 `rejection.rejected=true` 时，顶部显示醒目卡片，含 reason + scores + 建议改写
2. **三个 Gate Span 时间轴**: 串到现有 span 时间线，绿色 ✅ / 红色 ❌
3. **诊断折叠面板**: 展开后看到 `scores` vs `thresholds` 对比
4. **"知识缺口" 跳转按钮**: 高频拒答（`occurrence_count >= 5`）一键跳到知识库待办

---

## 六、风险分级与证据门槛矩阵

```python
# backend/config/rag.py 新增完整门槛矩阵

EVIDENCE_THRESHOLDS: dict[str, dict[str, float]] = {
    # 普通知识问答 — 宽松门槛（鼓励答）
    "summary_query": {
        "vec_min": 0.02,
        "rerank_top1": 0.30,
        "rerank_avg": 0.25,
        "faithfulness": 0.50,
    },
    "entity_query": {
        "vec_min": 0.03,
        "rerank_top1": 0.35,
        "rerank_avg": 0.28,
        "faithfulness": 0.50,
    },
    # 制度/流程查询 — 中等门槛
    "sop_query": {
        "vec_min": 0.04,
        "rerank_top1": 0.45,
        "rerank_avg": 0.35,
        "faithfulness": 0.60,
    },
    "policy_query": {
        "vec_min": 0.05,
        "rerank_top1": 0.55,
        "rerank_avg": 0.45,
        "faithfulness": 0.70,
    },
    # 财务/人事等高风险 — 最严格
    "compliance_query": {
        "vec_min": 0.06,
        "rerank_top1": 0.65,
        "rerank_avg": 0.55,
        "faithfulness": 0.80,
    },
}

# 风险等级 ← intent + 命中 doc_type 联合判定
RISK_LEVEL_MATRIX = {
    "policy_query": "high",
    "compliance_query": "high",
    "fact_query": "medium",      # 财务/人事通常以事实查询出现
    "summary_query": "low",
    "entity_query": "low",
}
```

门槛映射规则（写在 `_execute()`）:

```
intent → RISK_LEVEL_MATRIX[intent] → EVIDENCE_THRESHOLDS[matrix_key(intent, doc_type)]
                                       matrix_key 由 intent 主导，doc_type 升级兜底
                                       如果 doc_type ∈ {policy, compliance, legal} 即便 intent=summary 也按 "high" 计
```

具体取值策略:

| 场景 | 命中 doc_type | intent | 风险等级 | top1 门槛 | Faithfulness 门槛 |
|------|--------------|--------|---------|---------|-----------------|
| "今天天气" | general | summary_query | low | 0.30 | 0.50 |
| "Listing 怎么写" | sop | sop_query | medium | 0.45 | 0.60 |
| "退货政策" | policy | summary_query | high | 0.55 | 0.70 |
| "员工报销额度" | policy | fact_query | high | 0.65 | 0.80 |
| "GDPR 合规要求" | compliance | policy_query | high | 0.65 | 0.80 |

---

## 七、反向驱动知识库优化

### 7.1 拒答日志聚合

新增 [backend/rag/evidence_gap_analyzer.py](backend/rag/evidence_gap_analyzer.py)（cron 任务）:

```python
"""后台任务：聚合 Trace 中的拒答事件，生成知识缺口报告。"""

from collections import Counter
from backend.rag.trace_store import get_trace_store
from backend.rag.evidence_gate import RejectReason

def aggregate_gaps(since_hours: int = 24, min_occurrences: int = 3) -> list[dict]:
    """聚合最近 N 小时的拒答事件，按 question_hash 累计。
    
    Returns:
        list of gap signals with occurrence_count
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()

    store = get_trace_store()
    traces = store.list_since(cutoff, only_rejected=True)

    gap_map: dict[str, dict] = {}
    for trace in traces:
        gaps = trace.get("metadata", {}).get("knowledge_gaps", [])
        for g in gaps:
            qh = g["question_hash"]
            if qh not in gap_map:
                gap_map[qh] = g.copy()
                gap_map[qh]["occurrence_count"] = 0
                gap_map[qh]["first_seen"] = g["timestamp"]
            gap_map[qh]["occurrence_count"] += 1
            gap_map[qh]["last_seen"] = g["timestamp"]

    # 按出现次数排序
    sorted_gaps = sorted(
        gap_map.values(), key=lambda x: x["occurrence_count"], reverse=True
    )
    return [g for g in sorted_gaps if g["occurrence_count"] >= min_occurrences]


def generate_kb_optimization_report() -> dict:
    """生成优化报告，分三块:
    1. 高频未覆盖问题（需新增文档）
    2. 召回但 doc_type 不匹配的（需补充同主题文档）
    3. 检索到但 Rerank 失败的（需改善 chunk 质量）
    """
    gaps = aggregate_gaps()
    return {
        "missing_topics": [g for g in gaps
                          if g["reject_layer"] == "retrieval"
                          and g["reject_reason"] in ("no_retrieval", "low_similarity")],
        "doc_type_gap": [g for g in gaps
                        if g["reject_reason"] == "doc_type_mismatch"],
        "chunk_quality_issues": [g for g in gaps
                                if g["reject_layer"] == "rerank"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
```

### 7.2 缺口分类与处置建议

| 拒答原因 | 含义 | 处置 |
|---------|------|------|
| `no_retrieval` / `low_similarity` | 知识库无相关内容 | **新建文档**（按 expected_doc_types 归类） |
| `doc_type_mismatch` | 有内容但不是该类型 | **补充同主题 SOP/制度**（改写现有 general 文档） |
| `rerank_failed` | 检索到但相关性低 | **chunk 拆分**：长 chunk 切短 / 改写 chunk 开头概要 |
| `insufficient_evidence` | top1 高但 topK 弱 | **chunk 冗余**：补全同一主题多角度描述 |
| `hallucination_check_failed` | LLM 编造 | **补文档**：缺口主题文档；或**收紧 Prompt** |

### 7.3 三类驱动力

| 驱动力 | 频率 | 实现 |
|-------|------|------|
| **被动提醒** | 每次拒答 | Trace 详情页显示"是否反馈给知识库管理员"按钮 |
| **每日报告** | 24h | cron 生成 `kb_optimization_report` 邮件给 KB Owner |
| **主动预警** | 阈值触发 | 单题拒答 ≥ 5 次/24h → 实时 Slack/飞书通知（接 [orchestration/skills/email](backend/orchestration/skills/email/skill.py)） |

---

## 八、实施计划

按 P0 → P1 → P2 分期:

### 8.1 P0：核心 Retrieval Gate（1 sprint）

**目标**: 解决"空召回仍答"和"弱相关强答"两个 P0 问题。

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/config/rag.py` | 新增 `VEC_MIN_SCORE` / `DOC_TYPE_COVERAGE_REQUIRED` |
| 2 | `backend/rag/evidence_gate.py` | 新建文件，实现 `RejectReason` + `GateDecision` + `evidence_gate_retrieval()` |
| 3 | `backend/rag/retrieval/hybrid.py` | `hybrid_retrieve()` 末尾调用 `evidence_gate_retrieval()`，返回 `(docs, decision)` |
| 4 | `backend/rag/chain.py` | `_execute()` 检查 decision.passed，false 时走拒答路径 |
| 5 | `backend/tests/test_evidence_gate.py` | 单元测试：空 / 全低分 / doc_type 不匹配 / 正常 |
| 6 | 灰度：环境变量 `EVIDENCE_GATE_RETRIEVAL_ENABLED=false` 上线期间通过 |

### 8.2 P1：Rerank Gate + Generation Gate（1 sprint）

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/config/rag.py` | 阈值矩阵 `EVIDENCE_THRESHOLDS` + `RISK_LEVEL_MATRIX` |
| 2 | `backend/rag/evidence_gate.py` | `evidence_gate_rerank()` + `parse_llm_output()` |
| 3 | `backend/rag/reranker.py` | `rerank()` 出口插入 `evidence_gate_rerank()` |
| 4 | `backend/rag/chain.py` | `QA_PROMPT` 改写为 JSON 输出 |
| 5 | `backend/rag/chain.py` | `_execute()` 解析 LLM JSON，构造 `RejectInfo` |
| 6 | `backend/rag/tracer.py` | `SpanKind.RETRIEVAL_GATE/RERANK_GATE/FAITHFULNESS_GATE` |
| 7 | 测试 + 灰度 |

### 8.3 P2：Evaluation Gate + 反向驱动（1 sprint）

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/rag/chain.py` | `_evaluate()` 启用 Faithfulness，加 `HIGH_RISK_REJECT_SCORE` |
| 2 | `backend/rag/trace_store.py` | 新增 `list_since(cutoff, only_rejected=True)` |
| 3 | `backend/rag/evidence_gap_analyzer.py` | 聚合 + 报告生成 |
| 4 | `backend/app/api/routes/observability.py` | 新增 `GET /api/observability/knowledge-gaps` |
| 5 | `backend/app/api/cron/` | 注册每日运行任务 |
| 6 | 前端：Trace 详情页拒答横幅 + 知识缺口页 |

---

## 九、验收标准

### 9.1 功能验收

| 场景 | 期望 | 验证方式 |
|------|------|---------|
| 召回为空 | 返回 "知识库暂无相关资料" JSON | 单测 |
| top1 rerank < 0.30 | 触发 RERANK_FAILED 拒答 | 单测 |
| 命中 SOP 但问合规 | 触发 DOC_TYPE_MISMATCH | 单测 |
| LLM 编造数据 | Faithfulness < 0.50 → 整体拒答 | 单测 + 模型对比 |
| 同问题被拒答 5 次 | 触发知识缺口提醒 | 集成测试 |

### 9.2 性能验收

- 单次 Evidence Gate 总开销 ≤ 50ms（纯 Python + 配置查询）
- 拒答路径不调 LLM（仍走真实 LLM 生成再 evaluate 时，确保被切断）
- 灰度期间对比拒答率与人工评估"答得对"比例，**目标**：拒答率 ≤ 30%，人工评估答对率 ≥ 95%

### 9.3 可观测验收

- 每条拒答 Trace 都包含 `rejection` 字段完整填充
- 知识缺口报告每日 09:00 自动产出，邮件给 KB Owner
- 前端拒答横幅在弱网下首屏 ≤ 200ms 内渲染（数据已存在 Trace span 中）

---

## 十、风险与权衡

### 10.1 误拒风险

**风险**: 阈值设过严导致正常问题被拒。
**缓解**:
- 灰度发布（EVIDENCE_GATE_RETRIEVAL_ENABLED=false 旁路）
- 每 intent 类收集至少 50 条样本人工评估
- 阈值用 `os.getenv` 而非硬编码，方便回滚
- 提供 `/api/admin/evidence-thresholds` 端点支持运行时调整（备选）

### 10.2 性能开销

**风险**: 三层 Gate + Faithfulness NLI 增加延迟。
**缓解**:
- Retrieval Gate < 1ms（纯列表操作）
- Rerank Gate 与 Rerank span 并行，不额外延迟
- Faithfulness 默认开启（如延迟敏感可设回 false）

### 10.3 反向驱动噪音

**风险**: 用户测出问题大量触发 knowledge_gap，淹没真实缺口。
**缓解**:
- 聚合阈值 `min_occurrences ≥ 3` 过滤偶发
- 分类：单测 / 真实问题（人工标注或 LLM 分类）
- 反向驱动报告每周 / 每月聚合，非实时轰炸

---

## 十一、相关文件索引

### 新建

| 文件 | 职责 |
|------|------|
| `backend/rag/evidence_gate.py` | RejectReason + 三层 Gate 主逻辑 |
| `backend/rag/evidence_gap_analyzer.py` | 拒答日志聚合 + 报告 |
| `backend/tests/test_evidence_gate.py` | 三层 Gate 单测 |

### 修改

| 文件 | 改动 |
|------|------|
| `backend/config/rag.py` | 阈值矩阵 + 风险等级 |
| `backend/rag/retrieval/hybrid.py` | Retrieval Gate 集成 |
| `backend/rag/reranker.py` | Rerank Gate 集成 |
| `backend/rag/chain.py` | QA_PROMPT 改写 + JSON 解析 + Trace 串联 |
| `backend/rag/tracer.py` | `RejectInfo` + `SpanKind` 扩展 |
| `backend/rag/trace_store.py` | `list_since` 支持拒答过滤 |
| `backend/app/api/routes/observability.py` | 新增 knowledge-gaps 端点 |
| `frontend/src/types/trace.ts` | `Rejection` 类型扩展 |
| `frontend/src/app/observability/traces/[id]/page.tsx` | 拒答横幅 |
| `frontend/src/app/observability/knowledge-gaps/page.tsx` | 知识缺口聚合页（新） |

### 关联现有

- `backend/rag/guardrails/` — Evaluation Gate 直接复用，仅默认开关调整
- `backend/rag/indexing/indexer.py` — 反向驱动的"补文档"动作由此处执行
- `backend/orchestration/skills/email/` — 邮件通知复用

---

## 十二、与现有代码的冲突清单（设计自查）

> 评审时逐条核对，必须修复或缓解后才能进入实施。

### 12.1 致命冲突（阻断落地）

**C1. intent 名称虚构**
`backend/rag/retrieval/query_analyzer.py:70-100` 的 `classify_intent()` 只返回 7 个值：
`entity_query / order_query / inventory_query / ad_query / fact_query / report_query / summary_query`。
本方案使用的 `policy_query / compliance_query / sop_query` **不存在**。

- **方案 A**：扩展 `classify_intent`，新增三个分类（基于风险关键词复判）
- **方案 B**：改用 `RISK_LEVEL_MATRIX` 直接按 `intent + doc_type` 推导，去掉对 `policy_query` 等依赖
- **推荐 B**：改动面小、与现有分类解耦

**C2. JSON 输出与 Citation 体系冲突**
QA_PROMPT 改 JSON 输出后：
- `_format_references()` 依赖 `re.findall(r'\[(\d+)\]', answer)`（[chain.py:482](backend/rag/chain.py#L482)）提取内联引用
- JSON 内的 `citations: [1,2]` 是数组，不会产生 `[1]` 文本 → 参考文献版块整片空白
- SourceCard 失去依据

- **缓解**：QA_PROMPT 改用 **「先写 Markdown 答案 + 末尾 JSON 元数据」混合格式**：
  ```text
  正文 Markdown ...
  
  <!--META{"can_answer":true,"citations":[1,2],"confidence":0.92}-->
  ```
- 解析器读 META 注释，向后兼容现有 Markdown 渲染链路

**C3. JSON 输出与 Faithfulness 冲突**
`claim_extractor.extract_claims()` 用 `re.split(r'(?<=[。！？\n])', text)` 按句号切分。
JSON 一行无标点 → 整个 JSON 当一句，claim 提取数 = 0 → Faithfulness 静默跳过 → Evaluation Gate 失效。

- **缓解**：同 C2，改用「Markdown + 注释元数据」后，Faithfulness 直接处理 Markdown 正文
- 或者：`_evaluate()` 先解析 JSON 元数据，`can_answer=false` 直接跳过 Faithfulness

### 12.2 设计缺陷（落地后行为偏差）

**D1. `VEC_MIN_SCORE=0.02` 默认值无依据**
ChromaDB collection 的 distance 函数取决于创建时配置：
- cosine：`score = 1 - distance ∈ [-1, 1]`（余弦距离最大值 2）
- l2：`score = 1 / (1 + distance2)` 范围 ~[0, 1]

未实测两种配置就不能拍 0.02。

- **缓解**：首次部署跑 `backend/rag/evidence_gate.py collect_score_distribution()`：
  收集 1k 真实样本 → 输出 P50/P95 分位数 → 据此设阈值
- 阈值写入 `config.EVIDENCE_THRESHOLDS_SCORE_DISTRIBUTION` 注释里，标 "基于 N=1000 采样 2026-08-03"

**D2. Rerank Gate 接入位置说明不清晰**
[chain.py:200-203](backend/rag/chain.py#L200-L203) 是 `ContextualCompressionRetriever(RerankCompressor, base_retriever)` 包装，
compressor 内部把 filter 后的 docs 直接返回，score 已写入 metadata 但函数签名不返回。

- **修复**：明确写"Rerank Gate 必须在 `chain.invoke()` 后用 `result["context"][i].metadata["rerank_score"]` 反查"，
  不要塞进 `RerankCompressor.compress_documents` 内部

**D3. AdaptiveRetriever 扩展 chunk 污染 Rerank Gate**
[chain.py:194-197](backend/rag/chain.py#L194-L197) Adaptive 在 Rerank 之前，会注入同文档相邻 chunk。
扩展 chunk rerank_score 不一定高，会拉低 Gate 的 avg/gap 判断。

- **修复**：Gate 内过滤 `metadata.get("is_adaptive_expansion") is True` 的 chunk；
  上游 AdaptiveRetriever 标记 `metadata["is_adaptive_expansion"]=True` 与 `metadata["expand_source_id"]`

**D4. RAGChain 类字段缺失**
方案中使用了 `self._last_intent` / `self._risk_level` / `self._last_query_analysis`，当前 RAGChain 未初始化。

- **修复**：在 `RAGChain.__init__()` 内：
  ```python
  self._last_intent = "summary_query"
  self._risk_level = "low"
  self._last_query_analysis: ParsedQuery | None = None
  ```
  并在 `_prepare()` 内调用 `QueryAnalyzer` 解析后赋值（**让 QueryAnalyzer 跑一次而非在 chain 内部再跑**）

**D5. `_build_rejection_response()` / `risk_level_from_intent_and_doctype()` 未定义**

- **修复**：在 `evidence_gate.py` 完整实现：
  ```python
  def risk_level_from_intent_and_doctype(intent: str, doc_types: list[str]) -> str:
      high_risk_types = {"policy", "compliance", "legal"}
      if any(t in high_risk_types for t in doc_types):
          return "high"
      return RISK_LEVEL_MATRIX.get(intent, "low")

  def build_rejection_response(decision: GateDecision, layer: str) -> str:
      msg = REJECTION_FORMATS.get(decision.reason, "暂无可用信息。")
      return f"{msg}\n\n（拒绝层：{layer}）"
  ```

**D6. `parse_llm_output` 正则无法匹配 markdown JSON 块**

- **修复**：先提取 ```json ... ``` 块再解析：
  ```python
  m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
  if not m:
      m = re.search(r"\{.*\}", raw, re.DOTALL)
  json_str = m.group(1) if m and m.lastindex else (m.group(0) if m else "")
  ```
- 配套：**强制 QA_PROMPT 强调「输出 ```json 代码块`**（部分模型非代码块不输出 JSON）

### 12.3 配置/接口遗漏

**E1. 全部 Evidence Gate 配置常量缺失**
[config/rag.py](backend/config/rag.py) 当前没有：
`VEC_MIN_SCORE / DOC_TYPE_COVERAGE_REQUIRED / RERANK_MIN_TOP1 / RERANK_MIN_AVG / RERANK_MIN_GAP / RERANK_HIGH_RISK_MIN_TOP1 / FAITHFULNESS_REJECT_SCORE / HIGH_RISK_REJECT_SCORE / EVIDENCE_GATE_RETRIEVAL_ENABLED`

- **修复**：在 `rag.py` 末尾追加「Evidence Gate 配置」段，环境变量默认；
  并在 [config/__init__.py](backend/config/__init__.py) 重导出（按现有模式）

**E2. `trace_store.list_since()` 不存在**
[trace_store.py:106-126](backend/rag/trace_store.py#L106-L126) 只有 `list(limit)`。

- **修复**：新增 `list_since(cutoff_iso: str, only_rejected: bool = False, limit: int = 1000) -> list[dict]`
- **索引**：现有 `idx_ts_created` 已够（按 created_at 倒序扫描到 cutoff 即可）
- **SQL**：解析 JSON 后判断 `data.metadata.rejection.rejected == True`

**E3. `SpanKind` 枚举与 `_TYPE_INFER` 表不一致**
[tracer.py:27-35](backend/rag/tracer.py#L27-L35) 的 `_TYPE_INFER` 字典不含 `retrieval_gate / rerank_gate / faithfulness_gate`。

- **修复**：在 `_TYPE_INFER` 加：
  ```python
  "evidence_gate_retrieval": "retrieval_gate",
  "evidence_gate_rerank": "rerank_gate",
  "evidence_gate_faithfulness": "faithfulness_gate",
  ```
- 注意 `SpanKind` 枚举的 value 用 `retrieval_gate` 而不是 `RETRIEVAL_GATE`（小写是 tracer 内部约定）

**E4. 前端 `SPAN_TYPES` 列表未扩展**
[trace.ts:252-264](frontend/src/types/trace.ts#L252-L264) 不含新类型。

- **修复**：追加 `"retrieval_gate"` `"rerank_gate"` `"faithfulness_gate"`
- 同步追加 `SPAN_TYPE_LABELS`：`证据门-检索 / 证据门-Rerank / 证据门-忠实度`

**E5. 前端 `TraceRecord.rejection` 字段命名**
我方案用顶层 `rejection?: {...}`，但现有 `metadata: Record<string, unknown>` 也能容纳。

- **修复**：二选一
  - 方案 A（破坏类型但显式）：扩展 [trace.ts:129](frontend/src/types/trace.ts#L129) `TraceRecord` 加 `rejection?: RejectionInfo`
  - 方案 B（兼容）：用 `trace.metadata.rejection`（无需改类型，只是 object 类型不严谨）
  - **推荐 A**：可读性好，IDE 自动补全

**E6. KB Scope 白名单过度设计**
"未来扩展 KB Scope 白名单"是空想，当前 query_analyzer 没这概念。

- **修复**：删除 `OUT_OF_SCOPE` 相关段落；或明确标注「v2 概念，未在 P0-P2 实施」

**E7. cron 框架未知**
方案说 `backend/app/api/cron/`，但项目当前用什么调度框架未核实（APScheduler / celery / 自建）。

- **修复**：评审时确认现有调度方案；决定新增 `evidence_gap_analyzer.py` 是用现有框架还是新增入口

### 12.4 小问题

**F1. `GateDecision.diagnostics` key 不保证**
`build_gap_signal` 读 `reject_info.scores["top3_doc_types"]`，但 `GateDecision.diagnostics` 不强制包含此 key。

- **修复**：在 evidence_gate_retrieval/rerank 实现里硬约束 `diagnostics["top3_doc_types"]` / `["top3_sources"]` 必须填充
- 或者：build_gap_signal 改成读 `diagnostics` 而非 `scores`，统一入口

**F2. `multi_query._rewrite()` fallback 未告知**
方案说"rewrite 失败需在 Trace 里标注 fallback_used"但没说在哪一行加。

- **修复**：在 [multi_query.py:130-133](backend/rag/retrieval/multi_query.py#L130-L133) 的 except 块内：
  ```python
  trace_collector.add_event(span, "rewrite_fallback", "warn",
                            "LLM rewrite 失败，回退到单 query",
                            data={"fallback_query": question})
  ```
  span 已有，加 event 即可，不改架构

**F3. Rerank 阈值默认硬编码且未声明基于哪个模型**
BGE-reranker-base 和 bge-reranker-large 的 score 分布不同（前者更集中在 0.1-0.6，后者尾部更长）。

- **修复**：默认值加注释明确：
  ```python
  # 基于 bge-reranker-base（F:\models\bge-reranker-base）2026-08-03 校准
  RERANK_MIN_TOP1 = float(os.getenv("RERANK_MIN_TOP1", "0.35"))
  ```
  切换 `RERANKER_MODEL_PATH` 时需重新采样本调阈值（与 D1 配合）

**F4. 端点路由未实现**
`backend/app/api/routes/observability.py` 新增 `GET /api/observability/knowledge-gaps` 没说在主路由怎么注册。

- **修复**：查 `backend/app/server.py` 现有路由挂载方式（一般是 `app.include_router(...)`），文档里注明注册位置

**F5. Docker / 环境变量注入未提**
新加 7+ 个 `os.getenv`，需要：
- `.env.example` 同步更新
- `docker-compose.yml` / `restart_all.bat` 不需要改（uvicorn 会读 .env）

- **修复**：实施清单里加 [docs/operations/](docs/operations/) 配置清单文档更新

---

## 十三、最终评审准则

正式实施前必须满足：

- [ ] 上述 C1/C2/C3 三条致命冲突有解决方案（推荐：Markdown + 注释元数据方案 B）
- [ ] D1 阈值采样校准完成，附校准报告
- [ ] D2/D3 Rerank Gate 与 Adaptive 顺序关系在方案里说明白
- [ ] D4/D5 `RAGChain` 类内字段、`_build_rejection_response()` 在新文件完整定义
- [ ] D6 `parse_llm_output` 处理 markdown 代码块
- [ ] E1-E7 全部配置常量、TraceStore API、SpanKind 同步、前端类型、cron 框架核实完毕
- [ ] 灰度环境变量 `EVIDENCE_GATE_*_ENABLED=false` 上线时不影响存量流量
