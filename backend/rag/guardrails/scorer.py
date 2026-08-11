"""
scorer.py — Faithfulness 分数汇总 + 三级漏斗处理 + 统一入口

三级漏斗（按 NLI entailment prob 降级处理）:
  entail_prob > 0.5   → pass（通过）
  0.3 ~ 0.5           → mark（存疑标记 [?]）
  0.2 ~ 0.5（弱矛盾） → cite（退化为文档引用）
  < 0.2（强矛盾）      → rewrite（LLM 局部重写）
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from backend.config import ENABLE_FAITHFULNESS, FAITHFULNESS_SKIP_THRESHOLD
from backend.rag.guardrails.claim_extractor import extract_claims
from backend.rag.guardrails.risk_filter import filter_claims
from backend.rag.guardrails.nli_checker import check_claims_batch
from backend.rag.guardrails.nli_llm import evaluate_with_llm  # 2026-08-11 LLM-as-Judge
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
    core = re.sub(r'[^一-鿿\d]', '', claim)[:20]
    for line in text.split('\n'):
        stripped = line.strip()
        if core and core in re.sub(r'[^一-鿿\d]', '', stripped):
            return stripped
    return None


def _sanitize_chunk(text: str) -> str:
    """清洗文档内容：移除可能被 LLM 解析为指令注入的标记。"""
    text = re.sub(r'\[SYSTEM\].*?\[/SYSTEM\]', '[已过滤]', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'\[INST\].*?\[/INST\]', '[已过滤]', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<\|.*?\|>', '', text)
    text = re.sub(r'<system>.*?</system>', '[已过滤]', text, flags=re.IGNORECASE | re.DOTALL)
    return text


def rewrite_claim(claim: str, chunk: str) -> str:
    """LLM 局部重写：根据文档证据修正幻觉。

    🚨 已废弃（2026-08-10）：自动 rewrite 引入 30s 延迟 + 误判改坏风险，
    改为 sanitize_answer 内统一仅标记，不调用 LLM rewrite。
    保留此函数作为历史参考，不再被调用。

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

    🚨 已废弃（2026-08-10）：与 rewrite_claim 一起被 sanitize_answer 弃用。
    保留此函数作为历史参考。

    Args:
        claim: 被 NLI 判定为 contradiction_weak 的声明
        chunk: 支撑文档的原文

    Returns:
        "据文档记载：{chunk 原文}"
    """
    snippet = chunk[:300].strip().replace('\n', ' ')
    return f"据文档记载：{snippet}"


def sanitize_answer(answer: str, claim_results: List[ClaimResult]) -> str:
    """仅标记（不自动改写）—— 所有不可信 claim 在原文后追加 [??]*[存疑]。

    早期版本三分级（mark / cite / rewrite）改为统一标记，原因：
      - cite：原文被替换为 chunk 引用，破坏语义连贯性
      - rewrite：触发 LLM 局部改写，30s 阻塞 + 引入新错误
      - 实测数据（2026-08 用户日志）：NLI 误判 90%+，自动改写反而越改越错

    新策略：保留原答案 + 标记 [?]，让用户判断。
    未在原文中定位到的 claim → 末尾追加 ⚠️ 警告。
    """
    if not claim_results:
        return answer

    cleaned = answer
    for cr in claim_results:
        if cr.action == "pass":
            continue

        original_line = _match_line(cr.claim, cleaned)
        if not original_line:
            # 无法在原文中定位 → 在末尾追加存疑警告
            cleaned += f"\n\n> ⚠️*[存疑] 以下声明未被文档支撑：{cr.claim[:80]}*"
            logger.warning(f"[Faithfulness:sanitize] 无法定位，追加警告: {cr.claim[:40]}")
            continue

        # 统一标记：不改写、不删除、不替换原文
        replacement = f"{original_line} [??]*[存疑，未自动改写]*"
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

    # 3. NLI 验证（2026-08-11：支持 LLM-as-Judge 整体评估，2026-08-12）
    import os
    use_llm_judge = os.getenv("NLI_USE_LLM", "false").lower() == "true"
    if use_llm_judge:
        # 路径 A: LLM-as-Judge（整体评估，1 次调用，5-10s）
        verdict = evaluate_with_llm(answer_body, context_docs)
        if verdict.fallback:
            # 失败 fallback
            try:
                from backend.observability.metrics import nli_timeout_total
                nli_timeout_total.inc()
            except Exception:
                pass
            return FaithfulnessResult(
                score=verdict.score, total_claims=len(claims), high_risk_claims=len(high_risk),
                supported_claims=len(high_risk), unsupported_claims=0,
                claims=[], enabled=True,
            )
        # 把 verdict 转成 claim_results 格式（统一下游）
        nli_results = [
            {
                "claim": c, "supported": False, "best_score": 0.0,
                "best_chunk_preview": "", "label": "contradiction_strong", "action": "rewrite",
            }
            for c in verdict.unsupported_claims
        ] if verdict.unsupported_claims else [
            {
                "claim": "[整体可信]", "supported": True, "best_score": verdict.score,
                "best_chunk_preview": "", "label": "entailment", "action": "pass",
                "fallback_reason": "",  # LLM 真实校验
            }
        ]
        logger.info(
            f"[Faithfulness] LLM-Judge: score={verdict.score:.2f} "
            f"unsupported={len(verdict.unsupported_claims)} reason={verdict.reason[:50]}"
        )
        claim_results = []
        supported = sum(1 for _ in nli_results if _["supported"])
        unsupported = sum(1 for _ in nli_results if not _["supported"])
    else:
        # 路径 B: mDeBERTa 拆 claim（旧逻辑，30s+）
        nli_results = check_claims_batch(high_risk, context_docs)

        # 4. 汇总 + 三级分级
        claim_results = []
        supported = 0
        unsupported = 0
        fallback_count = 0  # 2026-08-11：NLI fallback 计数

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
            # 2026-08-11：检测 NLI fallback（避免静默成功）
            if r.get("fallback_reason"):
                fallback_count += 1

        # 2026-08-11：NLI fallback 警告 + 指标
        if fallback_count > 0:
            logger.warning(
                f"[Faithfulness] ⚠️ NLI 全部 fallback：{fallback_count}/{len(nli_results)} claims 未实际校验"
            )
            try:
                from backend.observability.metrics import nli_coverage_rate
                coverage = (len(nli_results) - fallback_count) / len(nli_results) if nli_results else 1.0
                nli_coverage_rate.set(coverage)
            except Exception:
                pass

    score = supported / len(nli_results) if nli_results else 1.0

    # P2: 50% 阈值保护 — NLI 误判保护
    # 当 unsupported 比例超过阈值（默认 50%）时，跳过 rewrite，保留原答案
    # 避免 NLI 模型在中文弱场景下大量误判后，LLM 反复改写引入更多错误
    unsupported_ratio = unsupported / len(nli_results) if nli_results else 0.0
    skip_rewrite = unsupported_ratio > FAITHFULNESS_SKIP_THRESHOLD
    if skip_rewrite:
        logger.warning(
            f"[Faithfulness] {unsupported}/{len(nli_results)} 不可信 "
            f"({unsupported_ratio:.0%} > {FAITHFULNESS_SKIP_THRESHOLD:.0%})，"
            f"可能为 NLI 模型误判，跳过 rewrite 保留原答案"
        )

    # 5. 三级漏斗修复
    problem_claims = [cr for cr in claim_results if cr.action != "pass"]
    if problem_claims and not skip_rewrite:
        cleaned = sanitize_answer(answer, problem_claims)
    else:
        cleaned = answer

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
