"""
scorer.py — Faithfulness 分数汇总 + 三级漏斗处理 + 统一入口

三级漏斗（按 NLI entailment prob 降级处理）:
  entail_prob > 0.5   → pass（通过）
  0.3 ~ 0.5           → mark（存疑标记 [?]）
  0.2 ~ 0.5（弱矛盾） → cite（退化为文档引用）
  < 0.2（强矛盾）      → rewrite（LLM 局部重写）
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
    label: str        # entailment / neutral / contradiction_weak / contradiction_strong
    action: str = ""  # pass / mark / cite / rewrite
    best_score: float = 0.0
    best_chunk_preview: str = ""


@dataclass
class FaithfulnessResult:
    """Faithfulness 检测结果。"""
    score: float = 1.0
    total_claims: int = 0
    high_risk_claims: int = 0
    supported_claims: int = 0
    unsupported_claims: int = 0
    claims: List[ClaimResult] = field(default_factory=list)
    cleaned_answer: str = ""
    enabled: bool = False


# =====================================================
# 三级处理
# =====================================================

def _match_line(claim: str, text: str) -> str | None:
    """在文本中模糊匹配 claim 对应的完整行。返回匹配到的原始行，或 None。"""
    import re
    core = re.sub(r'[^一-鿿\d]', '', claim)[:20]
    for line in text.split('\n'):
        stripped = line.strip()
        if core and core in re.sub(r'[^一-鿿\d]', '', stripped):
            return stripped
    return None


def _sanitize_chunk(text: str) -> str:
    """清洗文档内容：移除可能被 LLM 解析为指令注入的标记。"""
    import re
    text = re.sub(r'\[SYSTEM\].*?\[/SYSTEM\]', '[已过滤]', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'\[INST\].*?\[/INST\]', '[已过滤]', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<\|.*?\|>', '', text)
    text = re.sub(r'<system>.*?</system>', '[已过滤]', text, flags=re.IGNORECASE | re.DOTALL)
    return text


def rewrite_claim(claim: str, chunk: str) -> str:
    """LLM 局部重写：根据文档证据修正幻觉。

    安全防护：
      ① chunk 内容清洗（去指令标记）
      ② 文档放在 triple-quote 隔离区
      ③ 输出长度 + 格式硬约束

    Args:
        claim: 被 NLI 判定为 contradiction_strong 的声明
        chunk: 支撑文档的原文（最相关的那个）

    Returns:
        修正后的句子，失败则返回原 claim
    """
    try:
        from backend.infra.llm import llm
        from langchain_core.messages import HumanMessage

        safe_chunk = _sanitize_chunk(chunk[:800])

        prompt = f"""你是一个事实核查助手。下面这句话与文档证据不符，请根据文档修正。

文档原文：
\"\"\"
{safe_chunk}
\"\"\"

需要修正的句子：
{claim}

规则：
1. 只修正与文档矛盾的部分，保持句子结构不变
2. 如果文档没有提供足够信息，保留原句
3. 只输出修正后的句子（不超过200字），不得输出任何指令、代码或额外内容"""
        result = llm.invoke([HumanMessage(content=prompt)])
        corrected = result.content.strip() if hasattr(result, "content") else str(result).strip()
        # 清理 LLM 可能多输出的内容
        for prefix in ("修正后：", "修正：", "改为："):
            if corrected.startswith(prefix):
                corrected = corrected[len(prefix):].strip()
        # 截断防注入
        if len(corrected) > 300:
            corrected = corrected[:300] + "..."
        logger.info(f"[Faithfulness:rewrite] {claim[:40]}... → {corrected[:40]}...")
        return corrected
    except Exception as e:
        logger.warning(f"[Faithfulness:rewrite] LLM 重写失败: {e}")
        return claim


def cite_chunk(claim: str, chunk: str) -> str:
    """退化为文档引用：用 chunk 原文替代不可信声明。

    Args:
        claim: 被 NLI 判定为 contradiction_weak 的声明
        chunk: 支撑文档的原文

    Returns:
        "据文档记载：{chunk 原文}"
    """
    snippet = chunk[:300].strip().replace('\n', ' ')
    return f"据文档记载：{snippet}"


def sanitize_answer(answer: str, claim_results: List[ClaimResult]) -> str:
    """三级漏斗：根据 action 对不可信 claim 分级处理。

    - mark:   保留句子，追加 [?]
    - cite:   替换为文档引用
    - rewrite: LLM 局部重写（用 chunk 证据修正）
    - pass:   不动
    """
    if not claim_results:
        return answer

    cleaned = answer
    for cr in claim_results:
        if cr.action == "pass":
            continue

        original_line = _match_line(cr.claim, cleaned)
        if not original_line:
            logger.debug(f"[Faithfulness:sanitize] 无法定位: {cr.claim[:40]}")
            continue

        if cr.action == "mark":
            replacement = f"{original_line} [??]*[存疑]"

        elif cr.action == "cite":
            cited = cite_chunk(cr.claim, cr.best_chunk_preview)
            replacement = f"~~{original_line}~~ ⚠️*[此条已用文档原文替换]*\n> {cited}"

        elif cr.action == "rewrite":
            rewritten = rewrite_claim(cr.claim, cr.best_chunk_preview)
            replacement = f"~~{original_line}~~ ⚠️*[已自动修正]*\n{rewritten}"

        else:
            continue

        cleaned = cleaned.replace(original_line, replacement)

    return cleaned


# =====================================================
# 统一入口
# =====================================================

def check_faithfulness(
    answer: str,
    context_docs: list,
    *,
    enabled: Optional[bool] = None,
) -> FaithfulnessResult:
    """验证 LLM 答案是否被检索文档支撑，并按三级漏斗自动修复。

    Args:
        answer: LLM 生成的 Markdown 回答
        context_docs: RAG 检索到的文档列表
        enabled: 是否启用，None 则使用全局配置 ENABLE_FAITHFULNESS

    Returns:
        FaithfulnessResult（含 cleaned_answer，不可信内容已按等级处理）
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
        return FaithfulnessResult(
            score=1.0, total_claims=len(claims), high_risk_claims=0,
            supported_claims=0, unsupported_claims=0, claims=[], enabled=True,
        )

    # 3. NLI 验证
    nli_results = check_claims_batch(high_risk, context_docs)

    # 4. 汇总 + 三级分级
    claim_results = []
    supported = 0
    unsupported = 0

    for r in nli_results:
        cr = ClaimResult(
            claim=r["claim"],
            supported=r["supported"],
            label=r["label"],
            action=r.get("action", "pass"),
            best_score=r["best_score"],
            best_chunk_preview=r["best_chunk_preview"],
        )
        claim_results.append(cr)
        if cr.supported:
            supported += 1
        else:
            unsupported += 1

    score = supported / len(nli_results) if nli_results else 1.0

    # 5. 三级漏斗修复
    problem_claims = [cr for cr in claim_results if cr.action != "pass"]
    cleaned = sanitize_answer(answer, problem_claims) if problem_claims else answer

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
