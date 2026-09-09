"""生成质量指标 — 答案正确性 / 忠实度 / 分词辅助函数。"""
import re


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _tokenize_text(text: str) -> list[str]:
    """中英文混合分词：CJK 用 character bigrams，其余按空白分词。"""
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
    expected_steps = [s.strip() for s in expected.replace("；", ";").split(";") if s.strip()]
    if not expected_steps:
        return 1.0 if actual.strip() else 0.0

    actual_lower = actual.lower()
    covered = sum(1 for step in expected_steps if step.lower()[:10] in actual_lower)
    return covered / len(expected_steps)


def _comparative_coverage_score(actual: str, expected: str) -> float:
    """对比覆盖率：检查对比维度是否完整。"""
    expected_words = [w for w in expected.lower().split() if len(w) >= 2]
    if not expected_words:
        return 1.0

    actual_lower = actual.lower()
    covered = sum(1 for w in expected_words if w in actual_lower)
    return covered / len(expected_words)


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


def answer_correctness_typed(
    actual_answer: str,
    expected_answer: str,
    answer_type: str = "factual",
    must_contain: list[str] | None = None,
    must_not_contain: list[str] | None = None,
    numeric_tolerance: float = 0.01,
) -> dict[str, float]:
    """类型化的答案正确性评估。"""
    must_contain = must_contain or []
    must_not_contain = must_not_contain or []

    actual_lower = actual_answer.lower()
    expected_lower = expected_answer.lower()

    if must_contain:
        hits = sum(1 for kw in must_contain if kw.lower() in actual_lower)
        must_contain_hit = hits / len(must_contain)
    else:
        expected_keywords = _tokenize_text(expected_lower)
        if expected_keywords:
            hits = sum(1 for kw in expected_keywords if kw in actual_lower)
            must_contain_hit = hits / len(expected_keywords)
        else:
            must_contain_hit = 1.0 if actual_lower == expected_lower else 0.0

    violations = sum(1 for kw in must_not_contain if kw.lower() in actual_lower)
    must_not_contain_violation = 1.0 if violations > 0 else 0.0

    type_score = 0.0
    if answer_type == "numeric":
        type_score = _numeric_match_score(actual_answer, expected_answer, numeric_tolerance)
    elif answer_type == "procedural":
        type_score = _procedural_coverage_score(actual_answer, expected_answer)
    elif answer_type == "comparative":
        type_score = _comparative_coverage_score(actual_answer, expected_answer)
    else:
        type_score = _factual_overlap_score(actual_lower, expected_lower)

    correctness = must_contain_hit * 0.5 + type_score * 0.5
    if must_not_contain_violation > 0:
        correctness = 0.0

    return {
        "correctness": round(correctness, 4),
        "must_contain_hit": round(must_contain_hit, 4),
        "must_not_contain_violation": must_not_contain_violation,
        "type_specific_score": round(type_score, 4),
    }


def faithfulness_claim_based(
    answer: str,
    context: list[str],
    claims: list[str] | None = None,
) -> dict[str, float]:
    """基于声明分解的忠实度评估（简化版 RAGAS faithfulness）。"""
    if not answer.strip():
        return {"faithfulness": 1.0, "claim_count": 0, "supported_count": 0}

    if claims is None:
        claims = _split_claims(answer)

    if not claims:
        return {"faithfulness": 1.0, "claim_count": 0, "supported_count": 0}

    context_text = " ".join(context).lower()

    supported = 0
    for claim in claims:
        claim_tokens = _tokenize_text(claim)
        if not claim_tokens:
            supported += 1
            continue
        token_hits = sum(1 for tk in claim_tokens if tk in context_text)
        if token_hits / len(claim_tokens) >= 0.5:
            supported += 1

    faithfulness = supported / len(claims) if claims else 1.0

    return {
        "faithfulness": round(faithfulness, 4),
        "claim_count": len(claims),
        "supported_count": supported,
    }
