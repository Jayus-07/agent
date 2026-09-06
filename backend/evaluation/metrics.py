"""指标计算库 — 纯函数，无副作用，可直接用于 pytest 参数化。

V1.0 新增指标（覆盖召回/生成/可用性/性能/稳定性）：
- chunk_recall_at_k: 细粒度 chunk 级召回
- p95_latency: 95 分位响应时间
- reject_accuracy: out_of_scope 拒答准确率
- stability_variance: 同问异答方差（越小越稳定）
"""
import math
import re
import statistics
from itertools import combinations
from typing import Any


def recall_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """召回率@K：预期集中有多少出现在实际结果的前 K 个中。"""
    if not expected:
        return 1.0
    if not actual:
        return 0.0
    actual_set = set(actual[:k])
    hits = sum(1 for e in expected if e in actual_set)
    return hits / len(expected)


def mrr(actual: list[str], expected: list[str]) -> float:
    """Mean Reciprocal Rank：第一个相关结果排名的倒数均值。"""
    if not expected:
        return 1.0
    if not actual:
        return 0.0
    expected_set = set(expected)
    for i, item in enumerate(actual, start=1):
        if item in expected_set:
            return 1.0 / i
    return 0.0


def dcg_at_k(relevances: list[float], k: int) -> float:
    """Discounted Cumulative Gain。"""
    dcg = 0.0
    for i, rel in enumerate(relevances[:k]):
        # 使用标准 DCG 公式: rel / log2(i+2)
        dcg += rel / math.log2(i + 2)
    return dcg


def ndcg_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """Normalized DCG@K：考虑位置权重的排序质量。"""
    if not expected:
        return 1.0
    if not actual:
        return 0.0
    expected_set = set(expected)
    # 二值相关度：在期望集中=1，否则=0
    actual_relevances = [1.0 if item in expected_set else 0.0 for item in actual]
    # 理想排序：所有相关结果排在最前面
    ideal_relevances = [1.0] * min(len(expected), k)
    ideal_relevances += [0.0] * max(0, k - len(ideal_relevances))

    actual_dcg = dcg_at_k(actual_relevances, k)
    ideal_dcg = dcg_at_k(ideal_relevances, k)
    if ideal_dcg == 0.0:
        return 0.0
    return actual_dcg / ideal_dcg


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard 相似度：|A ∩ B| / |A ∪ B|。"""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / len(union)


def exact_match(actual: Any, expected: Any) -> float:
    """精确匹配，返回 0.0 或 1.0。"""
    return 1.0 if actual == expected else 0.0


def result_set_match(
    actual_rows: list[dict], expected_rows: list[dict], tolerance: float = 1e-6
) -> float:
    """SQL 结果集比对：行数一致 + 每行每列的值在 tolerance 内一致。"""
    if len(actual_rows) != len(expected_rows):
        return 0.0
    if not actual_rows and not expected_rows:
        return 1.0

    # 按所有列排序以消除行顺序差异
    def sort_key(row: dict) -> str:
        return str(sorted(row.items()))

    sorted_actual = sorted(actual_rows, key=sort_key)
    sorted_expected = sorted(expected_rows, key=sort_key)

    for a_row, e_row in zip(sorted_actual, sorted_expected):
        if set(a_row.keys()) != set(e_row.keys()):
            return 0.0
        for key in a_row:
            a_val = a_row[key]
            e_val = e_row[key]
            if isinstance(a_val, (int, float)) and isinstance(e_val, (int, float)):
                if abs(a_val - e_val) > tolerance:
                    return 0.0
            elif str(a_val) != str(e_val):
                return 0.0
    return 1.0


# ========== V1.0 新增指标 ==========

def chunk_recall_at_k(
    actual_chunks: list[str], expected_chunks: set[str] | list[str], k: int
) -> float:
    """Chunk 级召回 — 真实校验细粒度命中。

    Args:
        actual_chunks: 实际召回的 chunk_id 列表（已去重或不去重皆可）
        expected_chunks: 期望召回的 chunk_id 集合
        k: 仅看前 K 个结果

    Returns:
        float: 0.0~1.0，无 expected 时返回 1.0
    """
    if not expected_chunks:
        return 1.0
    expected = set(expected_chunks)
    top_k = set(actual_chunks[:k])
    return sum(1 for c in expected if c in top_k) / len(expected)


def p95_latency(durations_ms: list[int]) -> int:
    """95 分位响应时间（毫秒）。

    用于性能门禁 — 阈值建议 ≤ 3000ms。
    """
    if not durations_ms:
        return 0
    sorted_d = sorted(durations_ms)
    idx = int(len(sorted_d) * 0.95)
    # 边界保护：idx 可能等于 len
    idx = min(idx, len(sorted_d) - 1)
    return int(sorted_d[idx])


def reject_accuracy(
    results: list[Any], expected_reject_ids: set[str]
) -> float:
    """拒答准确率：out_of_scope 用例中系统主动说"无答案/资料未提及"的比例。

    Args:
        results: EvalResult 列表
        expected_reject_ids: 期望拒答的 case_id 集合

    Returns:
        float: 0.0~1.0
    """
    oos = [r for r in results if r.case_id in expected_reject_ids]
    if not oos:
        return 1.0
    rejected = sum(
        1 for r in oos
        if r.status == "pass" and r.metrics.get("reject_accuracy", 0.0) >= 1.0
    )
    return rejected / len(oos)


def stability_variance(answers: list[str]) -> float:
    """同问 N 次答案的稳定性方差 — 越小越稳定。

    Args:
        answers: 同一 question 跑 N 次得到的答案列表

    Returns:
        float: Jaccard 相似度的方差，0.0 表示完全一致。
        阈值建议: ≤ 0.02 表示稳定（方差），≤ 0.15 表示标准差稳定。
    """
    if len(answers) < 2:
        return 0.0
    pairs = [
        _string_jaccard(a, b) for a, b in combinations(answers, 2)
    ]
    return float(statistics.pvariance(pairs))


def _string_jaccard(a: str, b: str) -> float:
    """字符串级别的 Jaccard 相似度（基于字符 2-gram）。

    比集合更鲁棒 — 处理变长文本。
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not grams_a and not grams_b:
        return 1.0
    union = grams_a | grams_b
    return len(grams_a & grams_b) / len(union) if union else 0.0


def aggregate_metrics(results: list[Any]) -> dict[str, float]:
    """从 EvalResult 列表聚合统计指标（per-case 指标的均值）。

    Args:
        results: EvalResult 列表（必须有 .metrics 字段）

    Returns:
        dict[str, float]: {metric_name: avg_value}
    """
    agg: dict[str, list[float]] = {}
    for r in results:
        for k, v in (r.metrics or {}).items():
            if isinstance(v, (int, float)):
                agg.setdefault(k, []).append(float(v))
    return {k: round(sum(vs) / len(vs), 4) for k, vs in agg.items()}


# ========== V2.0 新增指标 ==========

def precision_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """精确率@K：实际结果前 K 个中有多少是相关的。

    与 recall_at_k 互补：recall 衡量"找到多少"，precision 衡量"找到的有多准"。
    用于检测检索噪声率（返回了不相关文档的比例）。

    Args:
        actual: 实际返回的 doc_id/chunk_id 列表
        expected: 期望相关的 doc_id/chunk_id 集合
        k: 仅看前 K 个结果

    Returns:
        float: 0.0~1.0，无 actual 或 k=0 时返回 0.0
    """
    if not actual or k <= 0:
        return 0.0
    expected_set = set(expected)
    top_k = actual[:k]
    relevant_count = sum(1 for item in top_k if item in expected_set)
    return relevant_count / len(top_k)


def context_noise_rate(actual: list[str], expected: list[str], k: int = 10) -> float:
    """上下文噪声率：实际结果中不相关文档的比例。

    与 precision_at_k 互补表达（noise = 1 - precision），但语义更直观：
    衡量检索系统引入的"干扰信息"量。高噪声会影响 LLM 生成质量。

    Args:
        actual: 实际返回的 doc_id/chunk_id 列表
        expected: 期望相关的 doc_id/chunk_id 集合
        k: 仅看前 K 个结果（默认 10，与 NDCG 对齐）

    Returns:
        float: 0.0~1.0，0.0 表示无噪声（全部相关）
    """
    prec = precision_at_k(actual, expected, k)
    return 1.0 - prec


def stage_retrieval_metrics(
    actual_docs: list[str],
    expected_docs: list[str],
    actual_chunks: list[str] | None = None,
    expected_chunks: list[str] | None = None,
    k_values: tuple[int, ...] = (1, 3, 5, 10),
) -> dict[str, float]:
    """检索阶段（Stage 1-3）综合指标。

    聚合 doc 级和 chunk 级的多指标，供 stage evaluation 使用。

    Returns:
        dict 包含:
        - doc_recall@K, doc_precision@K, doc_mrr, doc_ndcg@10
        - chunk_recall@K (if chunks provided)
        - top1_accuracy, context_noise@10
    """
    metrics: dict[str, float] = {}

    # Doc-level metrics
    for k in k_values:
        metrics[f"doc_recall@{k}"] = round(recall_at_k(actual_docs, expected_docs, k), 4)
        metrics[f"doc_precision@{k}"] = round(precision_at_k(actual_docs, expected_docs, k), 4)

    metrics["doc_mrr"] = round(mrr(actual_docs, expected_docs), 4)
    metrics["doc_ndcg@10"] = round(ndcg_at_k(actual_docs, expected_docs, 10), 4)
    metrics["context_noise@10"] = round(context_noise_rate(actual_docs, expected_docs, 10), 4)

    # Top-1 accuracy
    if expected_docs and actual_docs:
        metrics["top1_accuracy"] = 1.0 if actual_docs[0] in set(expected_docs) else 0.0
    elif not expected_docs and not actual_docs:
        metrics["top1_accuracy"] = 1.0  # 负样本正确拒答
    else:
        metrics["top1_accuracy"] = 0.0

    # Chunk-level metrics (if provided)
    if actual_chunks is not None and expected_chunks:
        for k in k_values:
            metrics[f"chunk_recall@{k}"] = round(
                chunk_recall_at_k(actual_chunks, expected_chunks, k), 4
            )

    return metrics


def stage_rerank_metrics(
    rerank_scores: list[float],
    relevant_mask: list[bool],
) -> dict[str, float]:
    """重排阶段（Stage 4）指标。

    评估 reranker 将相关文档排到前面的能力。

    Args:
        rerank_scores: 按 rerank 分数降序排列的分数列表
        relevant_mask: 对应位置是否相关（True/False）

    Returns:
        dict 包含:
        - avg_relevant_score: 相关文档的平均分
        - avg_noise_score: 不相关文档的平均分
        - score_separation: 分离度 = avg_relevant - avg_noise（越大越好）
        - top1_is_relevant: top-1 是否相关
        - top3_relevant_ratio: top-3 中相关的比例
    """
    if not rerank_scores or not relevant_mask:
        return {
            "avg_relevant_score": 0.0,
            "avg_noise_score": 0.0,
            "score_separation": 0.0,
            "top1_is_relevant": 0.0,
            "top3_relevant_ratio": 0.0,
        }

    relevant_scores = [s for s, r in zip(rerank_scores, relevant_mask) if r]
    noise_scores = [s for s, r in zip(rerank_scores, relevant_mask) if not r]

    avg_relevant = statistics.mean(relevant_scores) if relevant_scores else 0.0
    avg_noise = statistics.mean(noise_scores) if noise_scores else 0.0
    separation = avg_relevant - avg_noise

    top1_relevant = 1.0 if (relevant_mask and relevant_mask[0]) else 0.0
    top3_relevant_count = sum(1 for r in relevant_mask[:3] if r)
    top3_ratio = top3_relevant_count / min(3, len(relevant_mask))

    return {
        "avg_relevant_score": round(avg_relevant, 4),
        "avg_noise_score": round(avg_noise, 4),
        "score_separation": round(separation, 4),
        "top1_is_relevant": top1_relevant,
        "top3_relevant_ratio": round(top3_ratio, 4),
    }


def answer_correctness_typed(
    actual_answer: str,
    expected_answer: str,
    answer_type: str = "factual",
    must_contain: list[str] | None = None,
    must_not_contain: list[str] | None = None,
    numeric_tolerance: float = 0.01,
) -> dict[str, float]:
    """类型化的答案正确性评估。

    根据 answer_type 选择不同的评估策略：
    - "factual": 事实型，检查 must_contain 关键词覆盖
    - "numeric": 数值型，检查数值在 tolerance 范围内
    - "procedural": 流程型，检查关键步骤是否覆盖
    - "comparative": 对比型，检查对比维度是否完整

    Args:
        actual_answer: 系统生成的答案
        expected_answer: 期望的参考答案
        answer_type: 答案类型（factual/numeric/procedural/comparative）
        must_contain: 必须包含的关键词/数值列表
        must_not_contain: 不得包含的关键词列表
        numeric_tolerance: 数值容差（相对误差比例）

    Returns:
        dict 包含:
        - correctness: 总体正确性得分 0.0~1.0
        - must_contain_hit: must_contain 命中率
        - must_not_contain_violation: 是否有违禁内容（0=无违禁，1=有违禁）
        - type_specific_score: 类型特定得分
    """
    must_contain = must_contain or []
    must_not_contain = must_not_contain or []

    actual_lower = actual_answer.lower()
    expected_lower = expected_answer.lower()

    # 1. must_contain 命中率
    if must_contain:
        hits = sum(1 for kw in must_contain if kw.lower() in actual_lower)
        must_contain_hit = hits / len(must_contain)
    else:
        # 无显式 must_contain 时，用 expected_answer 的关键词近似
        expected_keywords = _tokenize_text(expected_lower)
        if expected_keywords:
            hits = sum(1 for kw in expected_keywords if kw in actual_lower)
            must_contain_hit = hits / len(expected_keywords)
        else:
            must_contain_hit = 1.0 if actual_lower == expected_lower else 0.0

    # 2. must_not_contain 违禁检查
    violations = sum(1 for kw in must_not_contain if kw.lower() in actual_lower)
    must_not_contain_violation = 1.0 if violations > 0 else 0.0

    # 3. 类型特定得分
    type_score = 0.0
    if answer_type == "numeric":
        type_score = _numeric_match_score(actual_answer, expected_answer, numeric_tolerance)
    elif answer_type == "procedural":
        type_score = _procedural_coverage_score(actual_answer, expected_answer)
    elif answer_type == "comparative":
        type_score = _comparative_coverage_score(actual_answer, expected_answer)
    else:  # factual
        type_score = _factual_overlap_score(actual_lower, expected_lower)

    # 4. 总体正确性 = must_contain_hit * 0.5 + type_score * 0.5，违禁时直接归零
    correctness = must_contain_hit * 0.5 + type_score * 0.5
    if must_not_contain_violation > 0:
        correctness = 0.0

    return {
        "correctness": round(correctness, 4),
        "must_contain_hit": round(must_contain_hit, 4),
        "must_not_contain_violation": must_not_contain_violation,
        "type_specific_score": round(type_score, 4),
    }


def _numeric_match_score(actual: str, expected: str, tolerance: float) -> float:
    """数值匹配得分：提取数字并检查相对误差。"""

    def extract_numbers(text: str) -> list[float]:
        return [float(n) for n in re.findall(r"[-+]?\d*\.?\d+", text)]

    actual_nums = extract_numbers(actual)
    expected_nums = extract_numbers(expected)

    if not expected_nums:
        return 1.0 if not actual_nums else 0.0
    if not actual_nums:
        return 0.0

    # 检查每个期望数值是否在 actual 中有匹配
    matches = 0
    for exp_num in expected_nums:
        for act_num in actual_nums:
            if exp_num == 0:
                if abs(act_num) <= tolerance:
                    matches += 1
                    break
            elif abs(act_num - exp_num) / abs(exp_num) <= tolerance:
                matches += 1
                break

    return matches / len(expected_nums)


def _procedural_coverage_score(actual: str, expected: str) -> float:
    """流程覆盖率：检查关键步骤词是否出现。"""
    # 简单实现：用句子分割，检查关键句是否覆盖
    expected_steps = [s.strip() for s in expected.replace("；", ";").split(";") if s.strip()]
    if not expected_steps:
        return 1.0 if actual.strip() else 0.0

    actual_lower = actual.lower()
    covered = sum(1 for step in expected_steps if step.lower()[:10] in actual_lower)
    return covered / len(expected_steps)


def _comparative_coverage_score(actual: str, expected: str) -> float:
    """对比覆盖率：检查对比维度是否完整。"""
    # 简单实现：检查 expected 中的关键词在 actual 中的覆盖
    expected_words = [w for w in expected.lower().split() if len(w) >= 2]
    if not expected_words:
        return 1.0

    actual_lower = actual.lower()
    covered = sum(1 for w in expected_words if w in actual_lower)
    return covered / len(expected_words)


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _tokenize_text(text: str) -> list[str]:
    """中英文混合分词：CJK 用 character bigrams，其余按空白分词。

    避免 ``str.split()`` 对无空格中文完全失效的问题。
    """
    text = text.lower()
    tokens: list[str] = []
    buf: list[str] = []
    buf_is_cjk = False

    def flush_buf() -> None:
        nonlocal buf_is_cjk
        if not buf:
            return
        segment = "".join(buf)
        if buf_is_cjk:
            tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
        else:
            tokens.extend(w for w in segment.split() if len(w) >= 2)
        buf.clear()
        buf_is_cjk = False

    for ch in text:
        is_cjk = bool(_CJK_RE.match(ch))
        if buf and is_cjk != buf_is_cjk:
            flush_buf()
        buf.append(ch)
        buf_is_cjk = is_cjk

    flush_buf()
    return tokens


def _split_claims(text: str) -> list[str]:
    """按中英文句号/问号/感叹号/分号/逗号分割声明。"""
    return [
        c.strip()
        for c in re.split(r"[。！？.!?；;，,、]", text)
        if c.strip()
    ]


def _factual_overlap_score(actual: str, expected: str) -> float:
    """事实重叠度：基于词重叠的简单得分。"""
    if not expected:
        return 1.0 if not actual else 0.0

    actual_tokens = set(_tokenize_text(actual))
    expected_tokens = set(_tokenize_text(expected))

    if not expected_tokens:
        return 1.0

    overlap = len(actual_tokens & expected_tokens)
    return overlap / len(expected_tokens)


def faithfulness_claim_based(
    answer: str,
    context: list[str],
    claims: list[str] | None = None,
) -> dict[str, float]:
    """基于声明分解的忠实度评估（简化版 RAGAS faithfulness）。

    将答案分解为独立声明（claims），检查每个声明是否有上下文支持。

    Args:
        answer: 生成的答案
        context: 检索到的上下文列表
        claims: 预分解的声明列表（可选，不提供时自动按句分割）

    Returns:
        dict 包含:
        - faithfulness: 有上下文支持的声明比例
        - claim_count: 声明总数
        - supported_count: 有支持的声明数
    """
    if not answer.strip():
        return {"faithfulness": 1.0, "claim_count": 0, "supported_count": 0}

    # 自动分解声明（按中英文句号、问号、感叹号、分号、逗号分割）
    if claims is None:
        claims = _split_claims(answer)

    if not claims:
        return {"faithfulness": 1.0, "claim_count": 0, "supported_count": 0}

    # 合并上下文为单一文本（保持原文用于子串匹配）
    context_text = " ".join(context).lower()

    # 检查每个声明是否有上下文支持
    # 用 _tokenize_text 提取 bigrams，检查是否在原文上下文中出现
    supported = 0
    for claim in claims:
        claim_tokens = _tokenize_text(claim)
        if not claim_tokens:
            supported += 1  # 无关键词的声明视为支持
            continue
        # 至少 50% token 在上下文中出现 → 视为支持
        token_hits = sum(1 for tk in claim_tokens if tk in context_text)
        if token_hits / len(claim_tokens) >= 0.5:
            supported += 1

    faithfulness = supported / len(claims) if claims else 1.0

    return {
        "faithfulness": round(faithfulness, 4),
        "claim_count": len(claims),
        "supported_count": supported,
    }
