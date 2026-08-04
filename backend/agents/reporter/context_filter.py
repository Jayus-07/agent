"""
context_filter.py — Context Filter: CrossEncoder 相关性验证 + 引用解析

对 RAG 检索结果做二次验证，过滤与问题无关的内容。
CrossEncoder 不可用时自动降级为 BM25 关键词匹配。

所有函数均为纯函数风格，不依赖任何 agent 框架。
"""

import re

from backend.config import RERANKER_THRESHOLD as _CONTEXT_RELEVANCE_THRESHOLD
from backend.shared.logger import logger


def check_reranker_available() -> bool:
    """检查 CrossEncoder 是否可用"""
    try:
        from backend.rag.reranker import reranker as _ce
        return _ce is not None
    except Exception:
        return False


def filter_step_results(step_results: dict, question: str) -> dict:
    """
    Context Filter: 过滤与问题无关的 RAG 检索结果。

    对每个 search_knowledge 步骤的输出，用 CrossEncoder 验证其与问题的相关性。
    低于阈值的输出替换为过滤标记，避免污染最终报告。

    Args:
        step_results: {step_id: {capability, output, status, ...}}
        question: 原始用户问题

    Returns:
        过滤后的 step_results（部分 RAG 输出可能被折叠）
    """
    if not step_results:
        return step_results

    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "rag.search" and sr.get("status") == "success"
    }

    if not rag_steps:
        return step_results

    if not check_reranker_available():
        return filter_by_bm25(step_results, question)

    rag_ids = []
    rag_texts = []
    for sid, sr in rag_steps.items():
        output = str(sr.get("output", ""))
        if output and len(output) > 20:
            rag_ids.append(sid)
            rag_texts.append(output[:800])

    if not rag_texts:
        return step_results

    try:
        from backend.rag.reranker import reranker as _ce
        from backend.config import RERANK_TIMEOUT
        from backend.infra.timeout import safe_call_with_timeout

        pairs = [(question, text) for text in rag_texts]
        scores = safe_call_with_timeout(
            _ce.predict, timeout=RERANK_TIMEOUT, default_value=None,
            error_message="Context Filter 超时", sentences=pairs,
        )
    except Exception as e:
        logger.warning(f"[ContextFilter] CrossEncoder 调用失败: {e}，降级为 BM25")
        return filter_by_bm25(step_results, question)

    if scores is None:
        logger.warning("[ContextFilter] 验证返回 None，降级为 BM25")
        return filter_by_bm25(step_results, question)

    filtered = dict(step_results)
    filtered_count = 0

    for sid, score in zip(rag_ids, scores):
        if float(score) < _CONTEXT_RELEVANCE_THRESHOLD:
            sr = dict(filtered[sid])
            original_output = str(sr.get("output", ""))
            sr["output"] = (
                f"*(此条检索结果与问题「{question[:40]}...」相关性较低 (score={float(score):.3f})，"
                f"已自动过滤。如需参考，原始内容如下)*\n\n"
                f"<details>\n<summary>展开原始内容 ({len(original_output)} 字符)</summary>\n\n"
                f"{original_output[:500]}\n\n</details>"
            )
            sr["_filtered"] = True
            sr["_relevance_score"] = round(float(score), 4)
            filtered[sid] = sr
            filtered_count += 1
            logger.info(
                f"[ContextFilter] 过滤 step={sid} "
                f"score={float(score):.3f} < {_CONTEXT_RELEVANCE_THRESHOLD}"
            )

    if filtered_count > 0:
        logger.info(f"[ContextFilter] 共过滤 {filtered_count}/{len(rag_ids)} 条无关 RAG 结果")

    return filtered


def filter_by_bm25(step_results: dict, question: str) -> dict:
    """BM25 关键词匹配过滤（CrossEncoder 不可用时的降级方案）"""
    # 延迟导入避免循环引用
    from backend.observability.alerts import make_alert, log_degradation

    alert = make_alert("RERANKER_UNAVAILABLE", {"question": question[:80]})
    log_degradation(alert)
    logger.warning("[ContextFilter] CrossEncoder 不可用，降级为关键词匹配过滤")

    _BM25_FALLBACK_THRESHOLD = 0.1

    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "rag.search" and sr.get("status") == "success"
    }
    if not rag_steps:
        return step_results

    q_chars = set(re.findall(r'[一-鿿]|\w+', question.lower()))

    filtered = dict(step_results)
    filtered_count = 0

    for sid, sr in rag_steps.items():
        output = str(sr.get("output", ""))
        if not output or len(output) <= 20:
            continue

        output_lower = output.lower()
        hits = sum(1 for c in q_chars if c in output_lower)
        hit_rate = hits / max(len(q_chars), 1)

        if hit_rate < _BM25_FALLBACK_THRESHOLD:
            original = dict(sr)
            original_output = str(original.get("output", ""))
            original["output"] = (
                f"*(此条检索结果与问题「{question[:40]}...」相关性较低 "
                f"(关键词命中率={hit_rate:.2%})，已自动过滤。"
                f"如需参考，原始内容如下)*\n\n"
                f"<details>\n<summary>展开原始内容 ({len(original_output)} 字符)</summary>\n\n"
                f"{original_output[:500]}\n\n</details>"
            )
            original["_filtered"] = True
            original["_relevance_score"] = round(hit_rate, 4)
            filtered[sid] = original
            filtered_count += 1
            logger.info(
                f"[ContextFilter-BM25] 过滤 step={sid} "
                f"hit_rate={hit_rate:.2%} < {_BM25_FALLBACK_THRESHOLD}"
            )

    if filtered_count > 0:
        logger.info(f"[ContextFilter-BM25] 共过滤 {filtered_count}/{len(rag_steps)} 条无关 RAG 结果")

    return filtered


def parse_sources_from_text(text: str) -> list[dict]:
    """
    从包含参考文献的文本中提取结构化来源。
    解析格式: "N. **filename** (type_label) — 相关度: 0.94"

    纯函数，无副作用。
    """
    type_label_map = {
        "Listing": "listing", "SOP": "sop", "广告政策": "ad_policy",
        "FAQ": "faq", "产品规格": "product_spec", "培训": "training",
        "制度规范": "policy", "报告": "report", "操作手册": "manual",
    }

    seen = {}
    for marker in ["### 参考文献", "### 参考来源"]:
        idx = text.find(marker)
        if idx == -1:
            continue
        ref_section = text[idx:]
        for line in ref_section.split("\n"):
            m = re.match(r'\d+\.\s*\*\*(.+?)\*\*\s*(?:\((.+?)\))?\s*(?:.*?相关度:\s*([\d.]+))?', line)
            if m:
                fname = m.group(1).strip()
                label = (m.group(2) or "").strip()
                score = float(m.group(3)) if m.group(3) else None
                if fname and fname not in seen:
                    doc_type = type_label_map.get(label, "general")
                    seen[fname] = {
                        "filename": fname,
                        "doc_type": doc_type,
                        "type_label": label or doc_type,
                        "score": round(score, 2) if score is not None else None,
                    }
        break

    return sorted(seen.values(), key=lambda s: s["filename"])


__all__ = [
    "filter_step_results",
    "filter_by_bm25",
    "check_reranker_available",
    "parse_sources_from_text",
]
