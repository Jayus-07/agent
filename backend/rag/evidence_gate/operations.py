"""Evidence Gate 门控逻辑 — 检索/重排序/评估三层 Gate + 辅助函数。

从 __init__.py 抽出，所有函数无状态、可独立测试。
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from backend.shared.logger import logger
from backend.rag.evidence_gate.models import (
    GateDecision, RejectInfo, RejectReason,
    REJECT_MESSAGES, HIGH_RISK_DOC_TYPES, INTENT_RISK_LEVEL,
)


# =====================================================
# 风险等级
# =====================================================

def risk_level_from_intent_and_doctype(intent: str, doc_types: list[str]) -> str:
    """按 intent + 命中 doc_type 推导风险等级 (§C1 修复，不依赖虚构 intent)。

    规则:
      - 命中 policy/compliance/legal → high
      - intent=fact_query + ≥1 命中 doc_type → high（财务人事事实组合）
      - 其余按 INTENT_RISK_LEVEL[intent]
      - 未知 intent → low (保守)
    """
    if any(dt in HIGH_RISK_DOC_TYPES for dt in doc_types):
        return "high"
    if intent == "fact_query" and doc_types:
        return "high"
    return INTENT_RISK_LEVEL.get(intent, "low")


# =====================================================
# Retrieval Gate
# =====================================================

def _safe_top_score(docs: list) -> float:
    """从 docs metadata 里提取 top1 相似度 (优先级: rerank_score > rrf_score > similarity > 0)。"""
    if not docs:
        return 0.0
    candidates = []
    for d in docs:
        s = (d.metadata.get("rerank_score")
             or d.metadata.get("rrf_score")
             or d.metadata.get("similarity")
             or 0.0)
        try:
            candidates.append(float(s))
        except (TypeError, ValueError):
            continue
    return max(candidates) if candidates else 0.0


# =====================================================
# 查询实体覆盖校验（P2，2026-08-21）
#
# 背景：hard negative（KB 含相关主题但无答案）的 rerank 分数落在正样本
# 主区间，纯分数阈值分不开；问题核心实体若不在召回内容中 → “主题相近
# 但无答案” → 拒答。离线评测已验证（拒答 75%→100%，正样本零误伤）。
#
# 生产链路与离线 runner 的差异：生产无法区分“该问题是否应拒答”，而
# paraphrase 问题的答案可能以同义词形式存在（同义词扩展召回的 chunk
# 用的是书面语），因此“存在”判定采用同义词闭包：实体本身或其任一同义词
# 出现即算存在，避免误伤改写提问。
# =====================================================

# 疑问词/代词/泛称不构成实体（与离线 runner 的 _QUERY_STOPWORDS 同源）
_QUERY_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "多久", "多少", "为什么",
    "请问", "你们", "我们", "贵公司", "公司", "需要", "应该", "可以", "是否",
    "有没有", "是什么", "进行", "相关", "具体", "一般", "规定", "要求",
    "时候", "目前", "现在", "支持", "采用", "包括", "属于", "关于", "一样",
}

# 生产链路额外泛称（双字高频词，几乎任何召回都难逐字覆盖，作实体必误拒）
_GATE_EXTRA_STOPWORDS = {
    "问题", "情况", "东西", "事情", "内容", "方式", "时间", "日期",
    "金额", "费用标准", "流程制度", "策略", "办法", "细则", "说明",
}


def _extract_gate_entities(query: str) -> list[str]:
    """jieba 分词提取查询候选实体（长度≥2、去停用词/纯数字）。"""
    import jieba
    return [
        w for w in jieba.cut(query)
        if len(w) >= 2 and w not in _QUERY_STOPWORDS
        and w not in _GATE_EXTRA_STOPWORDS and not w.isdigit()
    ]


def _entity_variants(term: str) -> set[str]:
    """实体的同义词闭包：自身 + SYNONYMS 正向/反向映射。

    反向映射保证 query 用“退货”、文档用“退款”时也能判存在
    （两者互为同义词组）。
    """
    synonyms = _SYNONYMS_VIEW()
    variants = {term}
    variants.update(synonyms.get(term, []))
    for key, syns in synonyms.items():
        if term in syns:
            variants.add(key)
    return variants


def _SYNONYMS_VIEW() -> dict:
    """延迟读取 SYNONYMS（避免模块导入期依赖预处理包）。"""
    from backend.rag.preprocessing.synonyms import SYNONYMS
    return SYNONYMS


def find_missing_entities(query: str, docs: list, top_n: int = 3) -> list[str]:
    """返回未见于 top_n 召回文本的“强实体”列表（空列表 = 不触发拒答）。

    存在判定用同义词闭包 + 去空白归一化（与离线 runner 口径一致）。
    拒答仅在存在“强实体”缺失时触发：强实体 = 含 CJK 且（长度≥3 或
    同义词词典收录）的词。双字泛称（问题/策略/流程…）与纯 ASCII 词
    不构成强实体，避免误拒；真实 hard negative 的核心实体（笔记本电脑/
    出口退税/预付款…）均长度≥3，不受影响。
    异常（如 jieba 缺失）时返回空列表 → 不干预原判（软降级）。
    """
    try:
        entities = _extract_gate_entities(query)
        if not entities:
            return []
        text = "".join(
            (getattr(d, "page_content", "") or "") for d in docs[:top_n]
        )
        text = "".join(text.split())
        missing = []
        for ent in entities:
            variants = _entity_variants(ent)
            covered = any("".join(v.split()) in text for v in variants)
            if covered:
                continue
            # 强实体规则：含 CJK 且（长度≥3 或词典收录）才触发拒答
            synonyms = _SYNONYMS_VIEW()
            is_dict_term = bool(synonyms.get(ent)) or any(
                ent in syns for syns in synonyms.values()
            )
            has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in ent)
            if has_cjk and (len(ent) >= 3 or is_dict_term):
                missing.append(ent)
        return missing
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[evidence_gate] 实体覆盖校验异常，跳过: {e}")
        return []


def evidence_gate_retrieval(
    docs: list,
    query_analysis=None,
    *,
    vec_min_score: float = 0.0,
    require_doc_type_coverage: bool = False,
    query: str | None = None,
    entity_check: bool = False,
    layer: str = "retrieval",
) -> GateDecision:
    """Retrieval Gate: 决定 docs 是否足够支撑回答。

    Args:
        docs: hybrid_retrieve 返回的 chunk 列表
        query_analysis: ParsedQuery (含 doc_types 字段)，None 则跳过 doc_type 覆盖
        vec_min_score: top1 最低相似度阈值 (从 config 注入，默认 0.2 对齐 RAGFlow)
        require_doc_type_coverage: 是否要求召回 doc_type 覆盖 QueryAnalyzer 推导的 doc_types
        query: 原始提问（P2 实体覆盖校验需要；None 则跳过）
        entity_check: 是否启用实体覆盖校验（GATE_ENTITY_CHECK_ENABLED 注入）
        layer: span label (默认 retrieval)

    Returns:
        GateDecision.passed=False 触发拒答; True 继续下游
    """
    # --- 1. 空召回 → NO_EVIDENCE ---
    if not docs:
        return GateDecision(
            passed=False, reason=RejectReason.NO_EVIDENCE, layer=layer,
            diagnostics={"doc_count": 0,
                         "threshold": {"vec_min": vec_min_score}},
        )

    # --- 2. top1 相关度过低 → LOW_RELEVANCE ---
    top_score = _safe_top_score(docs)
    if top_score < vec_min_score:
        return GateDecision(
            passed=False, reason=RejectReason.LOW_RELEVANCE, layer=layer,
            score=top_score,
            diagnostics={"doc_count": len(docs), "top_score": round(top_score, 4),
                         "threshold": {"vec_min": vec_min_score},
                         "top3_sources": [d.metadata.get("source_file", "")[:60]
                                          for d in docs[:3]]},
        )

    # --- 3. 查询实体覆盖校验 → NO_EVIDENCE（P2：主题相近但无答案） ---
    if entity_check and query:
        missing = find_missing_entities(query, docs)
        if missing:
            return GateDecision(
                passed=False, reason=RejectReason.NO_EVIDENCE, layer=layer,
                score=top_score,
                diagnostics={"doc_count": len(docs), "top_score": round(top_score, 4),
                             "entity_check": "fail",
                             "missing_entities": missing[:5]},
            )

    # --- 4. doc_type 覆盖检查 → DOC_TYPE_MISMATCH ---
    if require_doc_type_coverage and query_analysis is not None:
        expected = set(getattr(query_analysis, "doc_types", []) or [])
        if expected:
            actual = {d.metadata.get("doc_type", "") for d in docs
                      if d.metadata.get("doc_type")}
            if not (actual & expected):
                return GateDecision(
                    passed=False, reason=RejectReason.DOC_TYPE_MISMATCH, layer=layer,
                    score=top_score,
                    diagnostics={"expected_types": sorted(expected),
                                 "actual_types": sorted(actual),
                                 "doc_count": len(docs)},
                )

    # --- 通过 ---
    return GateDecision(
        passed=True, layer=layer, score=top_score,
        diagnostics={"doc_count": len(docs), "top_score": round(top_score, 4)},
    )


# =====================================================
# Rerank Gate (多维)
# =====================================================

def evidence_gate_rerank(
    docs: list,
    *,
    intent: str = "summary_query",
    risk_level: str = "low",
    # 阈值兜底值（与 config/rag.py 的 RERANK_MIN_* 默认一致）：
    # 调用方（chain.py）总是显式传入配置值，这里仅为向后兼容的签名默认。
    min_top1: float = 0.35,
    min_avg: float = 0.25,
    min_gap: float = 0.05,
    high_risk_min_top1: float = 0.55,
    layer: str = "rerank",
) -> GateDecision:
    """Rerank Gate: 多维分数判定 (§3.2)。

    维度:
      1. top1 ≥ min_top1 (高风险 high_risk_min_top1)
      2. topK_avg ≥ min_avg
      3. (可选) score_gap ≥ min_gap

    注意: chunk 已被 LangChain Compressor 用 RERANK_SCORE_THRESHOLD=0.3
          预过滤一次，这里是从已过滤列表二次判断。
    """
    if not docs:
        return GateDecision(
            passed=False, reason=RejectReason.NO_EVIDENCE, layer=layer,
            diagnostics={"doc_count": 0,
                         "threshold": {"min_top1": min_top1, "min_avg": min_avg}},
        )

    # 提取分数（按 metadata.rerank_score，缺失视为 0.5 [默认]）
    scores: list[float] = []
    for d in docs:
        s = d.metadata.get("rerank_score")
        try:
            scores.append(float(s) if s is not None else 0.5)
        except (TypeError, ValueError):
            scores.append(0.5)

    top1 = scores[0]
    avg = sum(scores) / len(scores)
    gap = (top1 - scores[-1]) if len(scores) > 1 else 0.0

    is_high_risk = risk_level == "high"
    effective_min_top1 = high_risk_min_top1 if is_high_risk else min_top1

    base_diag = {
        "doc_count": len(docs),
        "top1": round(top1, 4),
        "avg": round(avg, 4),
        "gap": round(gap, 4),
        "intent": intent,
        "risk_level": risk_level,
    }

    if top1 < effective_min_top1:
        return GateDecision(
            passed=False, reason=RejectReason.INSUFFICIENT, layer=layer,
            score=top1,
            diagnostics={**base_diag, "failed_rule": "top1",
                         "threshold": {"min_top1": effective_min_top1}},
        )

    if avg < min_avg:
        return GateDecision(
            passed=False, reason=RejectReason.INSUFFICIENT, layer=layer,
            score=avg,
            diagnostics={**base_diag, "failed_rule": "avg",
                         "threshold": {"min_avg": min_avg}},
        )

    if len(scores) > 1 and gap < min_gap:
        return GateDecision(
            passed=False, reason=RejectReason.INSUFFICIENT, layer=layer,
            score=gap,
            diagnostics={**base_diag, "failed_rule": "gap",
                         "threshold": {"min_gap": min_gap}},
        )

    return GateDecision(
        passed=True, layer=layer, score=top1,
        diagnostics=base_diag,
    )


# =====================================================
# Evaluation Gate (Faithfulness 拒答判定)
# =====================================================

def is_groundedness_acceptable(
    faithfulness_score: float,
    *,
    risk_level: str = "medium",
    low_threshold: float = 0.3,
    high_threshold: float = 0.7,
) -> tuple[bool, Optional[RejectReason]]:
    """Faithfulness 分数是否可接受。

    Returns:
        (passed, reason_if_failed)
        passed=True  → 不拒答
        passed=False → reason=HALLUCINATION
    """
    # Three-level thresholds: low(0.3) < medium(0.5) < high(0.7)
    threshold_map = {
        "high": high_threshold,     # 0.7 for FAQ/财务
        "medium": 0.5,              # Default policy/general
        "low": low_threshold,       # 0.3 for casual chat
    }
    threshold = threshold_map.get(risk_level, 0.5)  # Default to medium
    
    if faithfulness_score < threshold:
        return False, RejectReason.HALLUCINATION
    return True, None


# =====================================================
# Markdown + META 注释解析 (§0.4 修正：与 Citation 兼容)
# =====================================================

# 注释格式: <!--META{"key":"value"}-->  必须出现在文本末尾
_RE_META = re.compile(r"<!--\s*META\s*(\{.*?\})\s*-->", re.DOTALL)


def parse_meta_comment(raw: str) -> tuple[str, dict]:
    """从 LLM 输出的 Markdown 中提取 <!--META{...}--> 注释。

    Returns:
        (cleaned_markdown, meta_dict) — meta_dict 空 {} 表示无元数据
    """
    if not raw:
        return raw, {}
    m = _RE_META.search(raw)
    if not m:
        return raw.strip(), {}
    meta_raw = m.group(1)
    cleaned = (raw[:m.start()] + raw[m.end():]).strip()
    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        logger.warning("[evidence_gate] META 注释 JSON 解析失败: %s", meta_raw[:80])
        return cleaned, {}
    if not isinstance(meta, dict):
        return cleaned, {}
    return cleaned, meta


# =====================================================
# Rejection 响应构造（§D5 修复）
# =====================================================

def build_rejection_response(
    decision: GateDecision,
    layer: str,
    *,
    self_correction_attempted: bool = False,
) -> tuple[str, RejectInfo]:
    """构造拒答文本 + RejectInfo，返回 (answer_str, reject_info)。

    answer_str 可直接作为 RAG 最终输出（同时 append 到 Trace）。
    """
    msg = REJECT_MESSAGES.get(
        decision.reason,
        "知识库暂不支持该问题，请联系知识库管理员补充资料。",
    ) if decision.reason else ""

    if self_correction_attempted:
        msg += "\n（已尝试改写提问重新检索，仍未找到可靠答案）"

    reject_info = RejectInfo(
        rejected=True,
        layer=layer,
        reason=decision.reason.value if decision.reason else None,
        scores={k: v for k, v in decision.diagnostics.items()
                if k in ("top1", "avg", "gap", "top_score", "doc_count", "faithfulness_score")},
        thresholds=decision.diagnostics.get("threshold", {}),
        self_correction_attempted=self_correction_attempted,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    return msg, reject_info


# =====================================================
# 顶层开关读取（推迟 import 避免循环依赖）
# =====================================================

def is_evidence_gate_enabled() -> bool:
    """总开关 — 避免循环 import config。
    在 evidence_gate_enabled() 关闭时所有 Gate 都返回 passed=True。"""
    try:
        from backend.config import EVIDENCE_GATE_ENABLED
        return bool(EVIDENCE_GATE_ENABLED)
    except Exception:
        return False


def gate_retrieval_passthrough() -> GateDecision:
    """总开关关闭时，传给下游一个通过的判定，避免改动下游调用点。"""
    return GateDecision(passed=True, layer="retrieval",
                        diagnostics={"gate_bypassed": True})


__all__ = [
    "risk_level_from_intent_and_doctype",
    "evidence_gate_retrieval",
    "evidence_gate_rerank",
    "find_missing_entities",
    "is_groundedness_acceptable",
    "parse_meta_comment",
    "build_rejection_response",
    "is_evidence_gate_enabled",
    "gate_retrieval_passthrough",
]
