"""
scorer.py — Faithfulness 分数汇总 + 统一入口

check_faithfulness(answer, context_docs) → FaithfulnessResult
"""

from dataclasses import dataclass, field
from typing import List, Optional

from backend.config import ENABLE_FAITHFULNESS
from backend.rag.guardrails.claim_extractor import extract_claims
from backend.rag.guardrails.risk_filter import filter_claims
from backend.rag.guardrails.nli_checker import check_claims_batch
from backend.shared.logger import logger


@dataclass
class ClaimResult:
    claim: str
    supported: bool
    label: str  # entailment / neutral / contradiction
    best_score: float
    best_chunk_preview: str = ""


@dataclass
class FaithfulnessResult:
    """Faithfulness 检测结果。"""
    score: float = 1.0                    # 0.0 ~ 1.0，1.0 = 全部通过
    total_claims: int = 0
    high_risk_claims: int = 0
    supported_claims: int = 0
    unsupported_claims: int = 0
    claims: List[ClaimResult] = field(default_factory=list)
    cleaned_answer: str = ""               # 剔除不可信 claim 后的安全答案
    enabled: bool = False                  # 是否实际执行了检测


def sanitize_answer(answer: str, unsupported_claims: List[str]) -> str:
    """从答案中移除不可信的句子，替换为警告标记。

    对每个 unsupported claim，在答案中模糊匹配并替换。
    claim 可能已被 strip 编号前缀，所以用 substring 匹配而非精确匹配。
    """
    if not unsupported_claims:
        return answer

    cleaned = answer
    for claim in unsupported_claims:
        # 取 claim 的核心内容（取最长的连续中文字段作为匹配 key）
        import re
        core = re.sub(r'[^一-鿿\d]', '', claim)[:20]
        # 在原文中找包含该核心词的完整行
        for line in cleaned.split('\n'):
            stripped = line.strip()
            if core and core in re.sub(r'[^一-鿿\d]', '', stripped):
                # 匹配到了：替换整行
                cleaned = cleaned.replace(
                    stripped,
                    f"~~{stripped}~~ ⚠️*[此条存疑，已自动标记]*"
                )
                break
        else:
            # 行匹配失败，尝试 claim 本身的前 20 字符
            short = claim[:20]
            if short in cleaned:
                idx = cleaned.find(short)
                end = cleaned.find('\n', idx)
                if end == -1:
                    end = len(cleaned)
                original = cleaned[idx:end]
                cleaned = cleaned[:idx] + f"~~{original}~~ ⚠️*[此条存疑，已自动标记]*" + cleaned[end:]

    return cleaned


def check_faithfulness(
    answer: str,
    context_docs: list,
    *,
    enabled: Optional[bool] = None,
) -> FaithfulnessResult:
    """验证 LLM 答案是否被检索文档支撑。

    Args:
        answer: LLM 生成的 Markdown 回答
        context_docs: RAG 检索到的文档列表（LangChain Document）
        enabled: 是否启用，None 则使用全局配置 ENABLE_FAITHFULNESS

    Returns:
        FaithfulnessResult（如果 disabled，score=1.0 且 enabled=False）
    """
    effective_enabled = enabled if enabled is not None else ENABLE_FAITHFULNESS

    if not effective_enabled:
        return FaithfulnessResult(enabled=False)

    if not answer or not context_docs:
        return FaithfulnessResult(enabled=True)

    # 1. 提取事实断言
    claims = extract_claims(answer)
    if not claims:
        logger.info("[Faithfulness] 未提取到可验证的 claim")
        return FaithfulnessResult(enabled=True)

    # 2. 风险筛选
    high_risk, skip_claims = filter_claims(claims)
    logger.info(
        f"[Faithfulness] {len(claims)} claims → "
        f"{len(high_risk)} 高风险 + {len(skip_claims)} 跳过"
    )

    if not high_risk:
        # 全部是低风险（纯描述），跳过 NLI
        return FaithfulnessResult(
            score=1.0,
            total_claims=len(claims),
            high_risk_claims=0,
            supported_claims=0,
            unsupported_claims=0,
            claims=[],
            enabled=True,
        )

    # 3. NLI 验证高风险 claim
    nli_results = check_claims_batch(high_risk, context_docs)

    # 4. 汇总分数
    claim_results = []
    supported = 0
    unsupported = 0

    for r in nli_results:
        cr = ClaimResult(
            claim=r["claim"],
            supported=r["supported"],
            label=r["label"],
            best_score=r["best_score"],
            best_chunk_preview=r["best_chunk_preview"],
        )
        claim_results.append(cr)
        if cr.supported:
            supported += 1
        else:
            unsupported += 1

    score = supported / len(nli_results) if nli_results else 1.0
    unsupported_texts = [cr.claim for cr in claim_results if not cr.supported]
    cleaned = sanitize_answer(answer, unsupported_texts) if unsupported_texts else answer

    result = FaithfulnessResult(
        score=round(score, 4),
        total_claims=len(claims),
        high_risk_claims=len(high_risk),
        supported_claims=supported,
        unsupported_claims=unsupported,
        claims=claim_results,
        cleaned_answer=cleaned,
        enabled=True,
    )

    logger.info(
        f"[Faithfulness] 分数={result.score:.2f} "
        f"({supported}/{len(nli_results)} supported, {unsupported} unsupported)"
    )
    return result
