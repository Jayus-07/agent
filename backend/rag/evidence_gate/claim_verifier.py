"""claim_verifier.py — 程序化 Claim 校验（非 LLM，2026-08-12）

解决 QA LLM 编造数字/时效（如"24小时"），而 LLM-as-Judge 又错误验证通过的问题。

设计原则（区别于 LLM Judge）：
  - 确定性事实（数字+单位、日期、金额、时效）→ 零容忍，FAIL 即 FAIL
  - 纯数字（"6个步骤"）→ 不零容忍，避免"步骤1~6"误杀
  - 实体/关键词 → 仅辅助信号，不作为最终 PASS 依据
  - 每个 claim 必须精确定位到其 [En] 引用的 chunk 原文，不在整个 Context 找"类似内容"

链路:
  QA Answer → Citation Parser → Claim Verifier（程序化）→ Faithfulness Judge → Final Answer
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from backend.shared.logger import logger

# =====================================================
# 确定性事实模式：数字 + 单位
# =====================================================

# 时效单位（数字后面跟这些单位，才算"时效事实"，零容忍）
_TIME_UNITS = r"(小时|分钟|秒|天|日|工作日|周|个月|月|年|日内)"

# 数字 + 时效单位：24小时 / 24 小时 / 48小时内 / 30分钟 / 2个工作日
_TIME_FACT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*" + _TIME_UNITS)

# 百分比：5% / 5 %
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")

# 金额：24元 / 500美元 / 1000 人民币 / ￥99
_AMOUNT_RE = re.compile(
    r"(?:[￥¥$€£]\s*)?(\d+(?:\.\d+)?)\s*(元|美元|欧元|人民币|美金|万|亿)"
)

# 日期：2026年8月12日 / 8月12日 / 2026-08-12
_DATE_RE = re.compile(
    r"(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}月\d{1,2}日)"
)


@dataclass
class ClaimVerdict:
    """单个 claim 的校验结果。"""
    supported: bool
    claim: str = ""
    reason: str = ""                    # numeric_fact_not_supported / 通过
    missing_facts: list = field(default_factory=list)  # claim 有但 chunk 没有的事实


@dataclass
class ClaimVerifierResult:
    """整体校验结果。"""
    passed: bool
    failed_claims: list = field(default_factory=list)
    reason: str = ""
    detail: str = ""


def _extract_facts(text: str) -> set[str]:
    """从文本提取确定性事实（数字+单位、日期、金额、时效）。

    归一化：去空格，统一全角/半角，避免"24 小时" vs "24小时"误判。
    """
    facts: set[str] = set()

    # 时效（数字 + 单位）
    for m in _TIME_FACT_RE.finditer(text):
        facts.add(f"时长:{m.group(1)}{m.group(2)}")
    # 百分比
    for m in _PERCENT_RE.finditer(text):
        facts.add(f"百分比:{m.group(1)}")
    # 金额
    for m in _AMOUNT_RE.finditer(text):
        facts.add(f"金额:{m.group(1)}{m.group(2)}")
    # 日期
    for m in _DATE_RE.finditer(text):
        facts.add(f"日期:{m.group(0)}")

    return facts


def _normalize(text: str) -> str:
    """归一化：去空格 + 全角转半角。"""
    text = text.replace(" ", "").replace("　", "")
    # 全角数字/符号转半角
    fullwidth = "０１２３４５６７８９％．："
    halfwidth = "0123456789%.:"
    return text.translate(str.maketrans(fullwidth, halfwidth))


def verify_claim(claim: str, chunk_text: str) -> ClaimVerdict:
    """校验单个 claim 是否被 chunk 原文支撑。

    只做确定性事实校验（数字+单位/日期/金额/时效）。
    claim 有某个事实而 chunk 没有 → 判定为编造（numeric_fact_not_supported）。
    不做关键词覆盖率判断（那不可靠，留给 LLM Judge）。
    """
    claim_norm = _normalize(claim)
    chunk_norm = _normalize(chunk_text)

    claim_facts = _extract_facts(claim_norm)
    if not claim_facts:
        # claim 没有确定性事实（纯描述性内容）→ 程序化校验放行，交给 LLM Judge
        return ClaimVerdict(supported=True, claim=claim, reason="无确定性事实，交 LLM Judge")

    chunk_facts = _extract_facts(chunk_norm)
    missing = claim_facts - chunk_facts

    if missing:
        return ClaimVerdict(
            supported=False,
            claim=claim,
            reason="numeric_fact_not_supported",
            missing_facts=sorted(missing),
        )
    return ClaimVerdict(supported=True, claim=claim, reason="事实匹配")


# =====================================================
# Citation → 原文 定位
# =====================================================

# 匹配 [E1] [E2] 引用（与 DOCUMENT_PROMPT 的 Evidence E{index} 对齐）
_CITE_RE = re.compile(r"\[E(\d+)\]")


def _split_claims(answer: str) -> list[tuple[str, int]]:
    """把回答按 [En] 引用切分成 (claim_text, cited_index) 列表。

    策略：以 [En] 为锚点，每个 [En] 归属其前面的句子。
    无引用的文本跳过（不校验）。
    """
    # 找到所有 [En] 位置
    positions = [(m.start(), int(m.group(1))) for m in _CITE_RE.finditer(answer)]
    if not positions:
        return []

    claims: list[tuple[str, int]] = []
    for i, (pos, idx) in enumerate(positions):
        # 该 claim 的起点：上一个引用之后（或文本开头）
        start = positions[i - 1][0] + len(f"[E{positions[i-1][1]}]") if i > 0 else 0
        # 终点：当前引用之前
        claim_text = answer[start:pos].strip()
        # 去掉前导的换行/列表符号
        claim_text = re.sub(r"^[-*•\s]+", "", claim_text)
        if claim_text:
            claims.append((claim_text, idx))
    return claims


def verify_answer(answer: str, docs: list) -> ClaimVerifierResult:
    """对回答做整体 Claim 校验。

    流程：
      1. 从 answer 提取 (claim, [En]) 对
      2. 建立 index → chunk 原文映射
      3. 逐个 claim 做确定性事实校验
      4. 任一 claim FAIL → 整体 FAIL（数字编造零容忍，LLM Judge 不能覆盖）

    Returns:
        ClaimVerifierResult.passed=False 表示存在编造事实，必须拒答。
    """
    if not docs:
        return ClaimVerifierResult(passed=True, reason="无 context_docs，跳过校验")

    # index → chunk 原文
    doc_map: dict[int, str] = {}
    for d in docs:
        idx = d.metadata.get("index")
        if idx is not None:
            doc_map[int(idx)] = d.page_content if hasattr(d, "page_content") else str(d)

    claims = _split_claims(answer)
    if not claims:
        return ClaimVerifierResult(passed=True, reason="无引用标注，跳过校验")

    failed: list[ClaimVerdict] = []
    for claim_text, cited_idx in claims:
        chunk_text = doc_map.get(cited_idx, "")
        if not chunk_text:
            # 引用了一个不存在的 Evidence → 也算失败
            failed.append(ClaimVerdict(
                supported=False, claim=claim_text,
                reason="citation_not_found", missing_facts=[f"E{cited_idx}"],
            ))
            continue
        verdict = verify_claim(claim_text, chunk_text)
        if not verdict.supported:
            failed.append(verdict)

    if failed:
        detail_parts = []
        for f in failed:
            detail_parts.append(
                f"claim='{f.claim[:60]}' reason={f.reason} missing={f.missing_facts}"
            )
        logger.warning(
            f"[ClaimVerifier] {len(failed)}/{len(claims)} claims 校验失败: "
            f"{'; '.join(detail_parts[:3])}"
        )
        return ClaimVerifierResult(
            passed=False,
            failed_claims=failed,
            reason="numeric_fact_not_supported",
            detail="; ".join(detail_parts),
        )

    logger.info(f"[ClaimVerifier] {len(claims)} claims 全部通过确定性校验")
    return ClaimVerifierResult(passed=True)


__all__ = [
    "ClaimVerdict", "ClaimVerifierResult",
    "verify_claim", "verify_answer",
]
