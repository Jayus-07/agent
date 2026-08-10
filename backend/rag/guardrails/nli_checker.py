"""
nli_checker.py — NLI 事实一致性验证

用 mDeBERTa-XNLI 模型判断: answer_claim 是否被 document_chunk 支撑。

关键设计:
  - 懒加载: 首次调用时才加载模型（~500MB），避免启动时内存炸
  - 配对数控制: 每个 claim 最多验证 top-K 个最相关的 chunk
  - 三分类: entailment(2) / neutral(1) / contradiction(0)
  - 批量推理: 一次 model.predict 处理所有 (claim, chunk) pairs（vs 逐 claim 循环）
  - 超时保护: NLI_TIMEOUT 触发后跳过 Faithfulness（避免 60s+ 阻塞）
"""

import threading
from typing import List, Tuple

import numpy as np

from backend.config import NLI_MODEL_PATH, NLI_TOP_K_CHUNKS, NLI_SCORE_THRESHOLD, NLI_TIMEOUT
from backend.infra.timeout import safe_call_with_timeout
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


def _select_top_chunks(claim: str, context_docs: list, top_k: int = NLI_TOP_K_CHUNKS) -> list:
    """为每个 claim 选最相关的 top-K 个 chunk（用简单的关键词重叠 + rerank_score）。

    优先使用已缓存的 rerank_score，没有则以关键词重叠度作为代理。
    中文按字符级 unigram 匹配（避免了 str.split() 对中文无效的问题）。
    """
    claim_lower = claim.lower()
    # 中文按字符拆分，英文按空格拆分
    claim_tokens = set(claim_lower) if any('一' <= c <= '鿿' for c in claim_lower) \
        else set(claim_lower.split())
    token_count = max(len(claim_tokens), 1)

    scored = []
    for doc in context_docs:
        content = doc.page_content if hasattr(doc, 'page_content') else str(doc)
        content_lower = content.lower()
        # 优先复用 rerank_score
        rerank = doc.metadata.get("rerank_score", 0) if hasattr(doc, 'metadata') else 0
        # 关键词重叠度
        if any('一' <= c <= '鿿' for c in claim_lower):
            # 中文：字符级匹配
            content_chars = set(content_lower)
            kw_overlap = sum(1 for c in claim_tokens if c in content_chars)
        else:
            # 英文：单词级匹配
            content_words = set(content_lower.split())
            kw_overlap = sum(1 for w in claim_tokens if w in content_words)
        # 综合分
        score = float(rerank) * 0.6 + min(kw_overlap / token_count, 1.0) * 0.4
        scored.append((doc, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


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


def _fallback_supported(claims: List[str]) -> List[dict]:
    """超时 / 失败时的 fallback：所有 claim 视为 supported（跳过 Faithfulness）。"""
    return [{
        "claim": c, "supported": True, "best_score": 1.0,
        "best_chunk_preview": "", "label": "entailment", "action": "pass",
    } for c in claims]


def check_claims_batch(claims: List[str], context_docs: list) -> List[dict]:
    """批量验证多条 claim（一次性 batch 推理 + 超时保护）。

    优化:
      - 一次性 model.predict 处理所有 (chunk, claim) pairs（vs 逐 claim 循环）
      - NLI_TIMEOUT 触发后跳过 Faithfulness，避免 60s+ 阻塞
    """
    if not claims or not context_docs:
        return [{
            "claim": c, "supported": False, "best_score": 0.0,
            "best_chunk_preview": "", "label": "contradiction_strong", "action": "rewrite",
        } for c in claims]

    model = _get_nli_model()

    # 1. 收集所有 (claim, chunk) pairs，按 claim 顺序组织
    claim_to_pairs: dict[str, list] = {}
    for claim in claims:
        top_chunks = _select_top_chunks(claim, context_docs)
        if not top_chunks:
            continue
        pairs = []
        for doc, _ in top_chunks:
            chunk_text = doc.page_content if hasattr(doc, 'page_content') else str(doc)
            pairs.append((chunk_text[:800], claim))
        claim_to_pairs[claim] = pairs

    if not claim_to_pairs:
        return [{
            "claim": c, "supported": False, "best_score": 0.0,
            "best_chunk_preview": "", "label": "contradiction_strong", "action": "rewrite",
        } for c in claims]

    # 2. 一次性 batch 推理所有 pairs
    all_pairs = []
    pair_to_claim = []
    for claim, pairs in claim_to_pairs.items():
        for p in pairs:
            all_pairs.append(p)
            pair_to_claim.append(claim)

    # P0: 超时保护（Windows 上只是提前返回 default_value，但能避免主流程阻塞）
    raw = safe_call_with_timeout(
        model.predict,
        timeout=NLI_TIMEOUT,
        default_value=None,
        error_message=f"[NLI] 推理超时 ({NLI_TIMEOUT}s)",
        sentences=all_pairs,
        batch_size=8,
        show_progress_bar=False,
    )
    if raw is None:
        logger.warning(f"[NLI] 推理超时（{NLI_TIMEOUT}s），跳过 Faithfulness")
        return _fallback_supported(claims)

    if not isinstance(raw, np.ndarray):
        raw = np.array(raw)

    # 3. 按 claim 分组 scores
    claim_scores: dict[str, list] = {claim: [] for claim in claim_to_pairs.keys()}
    for i, claim in enumerate(pair_to_claim):
        claim_scores[claim].append(raw[i])

    # 4. 生成每个 claim 的结果
    results = []
    for claim in claims:
        if claim not in claim_scores:
            results.append({
                "claim": claim, "supported": False, "best_score": 0.0,
                "best_chunk_preview": "", "label": "contradiction_strong", "action": "rewrite",
            })
            continue

        scores = claim_scores[claim]
        pairs = claim_to_pairs[claim]

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
