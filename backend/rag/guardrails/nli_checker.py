"""
nli_checker.py — NLI 事实一致性验证

用 mDeBERTa-XNLI 模型判断: answer_claim 是否被 document_chunk 支撑。

关键设计:
  - 懒加载: 首次调用时才加载模型（~500MB），避免启动时内存炸
  - 配对数控制: 每个 claim 最多验证 top-K 个最相关的 chunk
  - 三分类: entailment(2) / neutral(1) / contradiction(0)
"""

import threading
from typing import List, Tuple

import numpy as np

from backend.config import NLI_MODEL_PATH, NLI_TOP_K_CHUNKS, NLI_SCORE_THRESHOLD
from backend.shared.logger import logger

_nli_model = None
_nli_lock = threading.Lock()


def _get_nli_model():
    """懒加载 NLI 模型（线程安全）。"""
    global _nli_model
    if _nli_model is not None:
        return _nli_model
    with _nli_lock:
        if _nli_model is not None:
            return _nli_model
        from sentence_transformers import CrossEncoder
        _nli_model = CrossEncoder(NLI_MODEL_PATH)
        logger.info(f"[NLI] 模型加载完成: {NLI_MODEL_PATH}")
        return _nli_model


def _cosine_similarity(a: list, b: list) -> float:
    """简单余弦相似度（避免引入额外依赖）。"""
    a_arr = np.array(a)
    b_arr = np.array(b)
    dot = np.dot(a_arr, b_arr)
    norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    return float(dot / norm) if norm > 0 else 0.0


def _select_top_chunks(claim: str, context_docs: list, top_k: int = NLI_TOP_K_CHUNKS) -> list:
    """为每个 claim 选最相关的 top-K 个 chunk（用简单的关键词重叠 + rerank_score）。

    优先使用已缓存的 rerank_score，没有则以关键词重叠度作为代理。
    """
    claim_lower = claim.lower()
    scored = []
    for doc in context_docs:
        content = doc.page_content if hasattr(doc, 'page_content') else str(doc)
        # 优先复用 rerank_score
        rerank = doc.metadata.get("rerank_score", 0) if hasattr(doc, 'metadata') else 0
        # 关键词重叠度
        kw_overlap = sum(1 for w in claim_lower.split() if w in content.lower())
        # 综合分
        score = float(rerank) * 0.6 + min(kw_overlap / max(len(claim_lower.split()), 1), 1.0) * 0.4
        scored.append((doc, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def check_claim(claim: str, context_docs: list) -> dict:
    """验证单个 claim 是否被 context_docs 支撑。

    Args:
        claim: 一条事实声明
        context_docs: RAG 检索到的文档列表（LangChain Document 对象）

    Returns:
        {
            "claim": str,
            "supported": bool,
            "best_score": float,         # 0=contradiction, 1=neutral, 2=entailment
            "best_chunk_preview": str,   # 最支撑的 chunk 前 200 字符
            "label": "entailment"|"neutral"|"contradiction",
        }
    """
    if not context_docs:
        return {
            "claim": claim,
            "supported": False,
            "best_score": 0.0,
            "best_chunk_preview": "",
            "label": "contradiction",
        }

    model = _get_nli_model()
    top_chunks = _select_top_chunks(claim, context_docs)

    best_score = 0.0
    best_chunk = ""

    for doc, _ in top_chunks:
        chunk_text = doc.page_content if hasattr(doc, 'page_content') else str(doc)
        preview = chunk_text[:800]

        # NLI: (premise=chunk, hypothesis=claim)
        try:
            logits = model.predict([(preview, claim)])
            # logits 是单个 float（CrossEncoder 输出），需要转换为概率
            # mDeBERTa-v3-xnli 输出原始 logits，需要 softmax
        except Exception as e:
            logger.warning(f"[NLI] 推理失败: {e}")
            continue

        score = float(logits) if isinstance(logits, (int, float)) else float(logits[0])

        if score > best_score:
            best_score = score
            best_chunk = preview

    # 三分类判断
    if best_score >= NLI_SCORE_THRESHOLD:
        label = "entailment"
        supported = True
    elif best_score <= (1.0 - NLI_SCORE_THRESHOLD) / 2:
        label = "contradiction"
        supported = False
    else:
        label = "neutral"
        supported = False

    return {
        "claim": claim,
        "supported": supported,
        "best_score": round(best_score, 4),
        "best_chunk_preview": best_chunk[:200] if best_chunk else "",
        "label": label,
    }


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Softmax 归一化，将 3-class logits 转为概率分布。"""
    exp = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
    return exp / np.sum(exp, axis=-1, keepdims=True)


def _entailment_prob(logits: np.ndarray) -> float:
    """从 [entailment, neutral, contradiction] logits 中提取 entailment 概率。"""
    probs = _softmax(logits)
    return float(probs[0])  # entailment 是第0类


def _classify(entail_prob: float) -> tuple[str, str, bool]:
    """三级漏斗：根据 entailment 概率返回 (label, action, supported)。

    entail_prob > 0.5   → entailment           → pass（通过）
    0.3 ~ 0.5           → neutral              → mark（存疑标记 [?]）
    0.2 ~ 0.5（弱矛盾） → contradiction_weak    → cite（退化为文档引用）
    < 0.2（强矛盾）      → contradiction_strong  → rewrite（LLM 局部重写）
    """
    if entail_prob > NLI_SCORE_THRESHOLD:
        return "entailment", "pass", True
    if entail_prob >= 0.3:
        return "neutral", "mark", False
    if entail_prob >= 0.2:
        return "contradiction_weak", "cite", False
    return "contradiction_strong", "rewrite", False


def check_claims_batch(claims: List[str], context_docs: list) -> List[dict]:
    """批量验证多条 claim。

    优化: 用 NLI 模型的批量推理能力一次处理多个 (chunk, claim) 对。
    """
    if not claims or not context_docs:
        return [{
            "claim": c, "supported": False, "best_score": 0.0,
            "best_chunk_preview": "", "label": "contradiction_strong", "action": "rewrite",
        } for c in claims]

    model = _get_nli_model()
    results = []

    for claim in claims:
        top_chunks = _select_top_chunks(claim, context_docs)
        if not top_chunks:
            results.append({
                "claim": claim, "supported": False, "best_score": 0.0,
                "best_chunk_preview": "", "label": "contradiction",
            })
            continue

        # 对同一 claim 的所有候选 chunk 批量推理
        pairs = []
        for doc, _ in top_chunks:
            chunk_text = doc.page_content if hasattr(doc, 'page_content') else str(doc)
            pairs.append((chunk_text[:800], claim))

        try:
            raw = model.predict(pairs)
            # mDeBERTa-XNLI 返回 [[e, n, c], ...] 3-class logits
            scores = raw if isinstance(raw, np.ndarray) else np.array(raw)
        except Exception as e:
            logger.warning(f"[NLI] 批量推理失败: {e}")
            results.append({
                "claim": claim, "supported": False, "best_score": 0.0,
                "best_chunk_preview": "", "label": "contradiction",
            })
            continue

        # 取每个 pair 的 entailment 概率，找最高的
        best_idx = 0
        best_prob = 0.0
        for i, logits in enumerate(scores):
            prob = _entailment_prob(logits)
            if prob > best_prob:
                best_prob = prob
                best_idx = i

        best_chunk = pairs[best_idx][0] if best_idx < len(pairs) else ""
        label, action, supported = _classify(best_prob)

        results.append({
            "claim": claim,
            "supported": supported,
            "best_score": round(best_prob, 4),
            "best_chunk_preview": best_chunk[:200],
            "label": label,
            "action": action,
        })

    return results
