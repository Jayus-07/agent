"""metadata_router.py — 元数据级联路由（规划阶段 2.2 / 3.1）。

先便宜后贵：L0 文件名/路径强先验 → L1 taxonomy 嵌入检索 → L2 候选内词表
复核 → L3 LLM 单次 schema 抽取。L0-L2 零 LLM 成本；全部未命中才落 L3。

- L0/L1/L2 命中时结构字段（summary/keywords/entities）由调用方走规则提取
  （MetadataStage.finalize_cascade → finalize_unified 同一收口契约），下游零感知。
- 规则词表（DOC_TYPE_RULES / *_TYPE_HINTS）在本模块只读不新增——规则链已
  冻结（规划 §0.2 N7），新增正则必须走基线更新审批。
- L1 的 LR 训练位（规划阶段 3.2）：黄金集就绪后在 _TaxonomyIndex.classify
  与 L1 判定之间插入 LR 打分即可，接口不变。
- 每层判定与超时/回退见规划文档 §7.1 决策表 v0；阈值在 config/rag.py
  （METADATA_CASCADE_*），待影子模式 + 黄金集调优后定版（阶段 3.3/5）。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field

from backend.observability.metrics import metadata_route_total
from backend.observability.tracer import SpanKind, trace_collector
from backend.shared.logger import logger

# 级联层置信度语义：L1 的 confidence = cosine 相似度本身（0.6~1.0），
# L0/L2 为固定先验值（config 可调）。confidence 仅供下游展示与路由决策，
# 非概率校准——与规则链置信度口径一致（top/(top+second) 同为启发式）。


@dataclass
class CascadeDecision:
    """级联路由决策（可 JSON 化，进 trace / llm_decision）。"""
    level: str                       # L0 | L1 | L2 | L3
    doc_type: str                    # L3 时以 llm_result["doc_type"] 为准
    confidence: float = 0.0
    evidence: dict = field(default_factory=dict)
    llm_result: dict | None = None   # 仅 L3：单次抽取产物（None = LLM 失败）
    latency_ms: float = 0.0


# ====================================
# taxonomy 描述（规划阶段 3.1：嵌入索引入料）
# 描述 = 各 doc_type 强信号词的自然语言铺陈，供嵌入检索区分；改描述会
# 改变 L1 相似度分布 → 属阈值调优范畴，需重新跑黄金集评估。
# ====================================
TAXONOMY_DESCRIPTIONS: dict[str, str] = {
    "listing": "电商商品 Listing 文案与优化：五点描述、A+ 内容、主图附图规范、"
               "标题公式、搜索词与关键词策略、BSR 排名、变体父子体、类目节点、卖点提炼",
    "sop": "标准操作流程 SOP：作业指导书、操作手册、操作规程、岗位操作规范、"
           "步骤说明、标准作业程序 work instruction step by step",
    "ad_policy": "广告投放政策与规范：Amazon Ads 广告政策、竞价策略 bidding、"
                 "PPC 广告活动与广告组、投放规则、广告合规要求、品牌推广 CPC",
    "faq": "常见问题 FAQ 问答文档：售后 FAQ、退换货政策、退款流程、物流时效答疑、"
           "帮助中心、客户问答 troubleshooting return refund policy",
    "product_spec": "产品规格说明书：规格参数表、技术参数、材质说明、使用手册 "
                    "user manual、保养指南、故障排查、配置清单 datasheet",
    "training": "培训材料：入职培训、新人手册、岗前培训、培训课件与课程、"
                "考核带教、学习手册 onboarding training material",
    "policy": "公司管理制度：管理条例、管理办法、规章制度、审批规定、实施细则、"
              "工作守则、管控要求 policy",
    "compliance": "合规法规文档：监管要求、数据保护、隐私政策、GDPR CCPA、"
                  "网络安全法、等保备案、合规审查、行政处罚 regulatory compliance",
    "legal": "法律合同文书：合同条款、甲方乙方、违约责任、保密协议、知识产权、"
             "诉讼仲裁、第 N 条条款编号 terms and conditions contract clause",
    "security": "信息安全管理：访问控制、权限管理、加密、漏洞、渗透测试、防火墙、"
                "身份认证、零信任、安全审计 encryption access control vulnerability",
    "financial": "财务文档：报销制度、预算管理、发票付款审批、对账坏账、利润表"
                 "资产负债表现金流量表、税务薪酬核算 financial statement budget",
    "customer_data": "客户数据与个人信息文档：客户数据收集、用户画像、隐私数据保护、"
                     "个人信息保护、数据脱敏、数据泄露 PII data breach",
    "contract_template": "合同模板范本库：标准合同模板、协议范本、标准条款 boilerplate、"
                         "格式合同、模板库 contract template",
    "general": "通用文档：使用说明、操作指南、读书笔记、README、CHANGELOG、"
               "会议纪要、周报或其他无法归入上述类型的日常资料",
}


class _TaxonomyIndex:
    """taxonomy 描述嵌入索引（进程内缓存，阶段 3.1）。

    索引签名 = embedding 对象 id + 模型名：embedding 实例或模型变化时重建。
    """

    def __init__(self) -> None:
        self._sig: tuple | None = None
        self._labels: list[str] = []
        self._matrix = None  # numpy (N, D)，L2 归一化

    async def _ensure(self, embedding) -> None:
        sig = (id(embedding),
               getattr(embedding, "model_name", "") or str(getattr(embedding, "model", "")))
        if self._sig == sig and self._matrix is not None:
            return
        import numpy as np
        labels = list(TAXONOMY_DESCRIPTIONS.keys())
        descs = [TAXONOMY_DESCRIPTIONS[l] for l in labels]
        vecs = await asyncio.to_thread(embedding.embed_documents, descs)
        m = np.asarray(vecs, dtype="float32")
        norm = np.linalg.norm(m, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        self._matrix = m / norm
        self._labels = labels
        self._sig = sig

    async def classify(self, text: str, embedding, timeout: float) -> list[tuple[str, float]] | None:
        """返回 [(doc_type, sim)] 按相似度降序；embedding 异常/超时返回 None。"""
        try:
            await asyncio.wait_for(self._ensure(embedding), timeout)
            q = await asyncio.wait_for(
                asyncio.to_thread(embedding.embed_query, text[:2000]), timeout)
            import numpy as np
            qv = np.asarray([q], dtype="float32")
            qn = np.linalg.norm(qv, axis=1, keepdims=True)
            qn[qn == 0] = 1.0
            sims = (qv / qn) @ self._matrix.T          # (1, N)
            order = np.argsort(-sims[0])
            return [(self._labels[i], float(sims[0][i])) for i in order]
        except Exception as e:
            logger.warning(f"[MetaRouter] taxonomy 嵌入索引失败（跳过 L1/L2 → L3）: {e}")
            return None


_taxonomy_index = _TaxonomyIndex()


def _l0_strong_prior(filename: str, file_path: str) -> CascadeDecision | None:
    """L0：文件名 + 路径强先验。命中类型唯一（互不冲突）才定案。"""
    from backend.config.rag import FILENAME_TYPE_HINTS, FOLDER_TYPE_HINTS

    hits: set[str] = set()
    evidence: dict = {"filename_hits": [], "folder_hits": []}
    if filename:
        fname = os.path.splitext(filename)[0].lower()
        for hint, t in FILENAME_TYPE_HINTS.items():
            if hint.lower() in fname:
                evidence["filename_hits"].append(hint)
                hits.add(t)
    if file_path:
        parts = os.path.dirname(file_path).lower().replace("\\", "/").split("/")
        for hint, t in FOLDER_TYPE_HINTS.items():
            if hint.lower() in parts:
                evidence["folder_hits"].append(hint)
                hits.add(t)
    if len(hits) == 1:
        from backend.config.rag import METADATA_CASCADE_L0_CONFIDENCE
        return CascadeDecision(level="L0", doc_type=next(iter(hits)),
                               confidence=METADATA_CASCADE_L0_CONFIDENCE,
                               evidence=evidence)
    return None


def _l2_aggregate(text: str, candidates: list[str], min_gap: int, min_score: int) -> CascadeDecision | None:
    """L2：L1 召回的候选类型内用冻结词表重新计分（向量召回 + 词表重排）。

    只在候选内比较（不引入候选外类型），gap 足够大才定案。
    """
    from backend.config.rag import DOC_TYPE_RULES

    text_lower = text[:6000].lower()
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for dt in candidates:
        for pattern, weight in DOC_TYPE_RULES.get(dt, []):
            try:
                found = re.findall(pattern, text_lower)
            except re.error:
                continue
            if found:
                scores[dt] = scores.get(dt, 0) + weight * len(found)
                hits.setdefault(dt, []).append(pattern)
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) >= 2 else 0
    if top_score >= min_score and (top_score - second_score) >= min_gap:
        from backend.config.rag import METADATA_CASCADE_L2_CONFIDENCE
        return CascadeDecision(
            level="L2", doc_type=top_type, confidence=METADATA_CASCADE_L2_CONFIDENCE,
            evidence={"scores": scores, "top_hits": hits.get(top_type, [])[:5]})
    return None


def _rule_domain(text: str) -> str:
    """规则业务域（零 LLM）——级联命中时补齐 business_domain 字段。"""
    try:
        from backend.rag.preprocessing.metadata import detect_business_domain
        return detect_business_domain(text)[0] or "general"
    except Exception as e:
        logger.debug(f"[MetaRouter] 规则业务域识别失败，回落 general: {e}")
        return "general"


async def cascade_route(
    full_text: str,
    filename: str,
    file_path: str = "",
    embedding=None,
    parent_span_id: str = "",
) -> CascadeDecision:
    """元数据级联路由主入口（async）。

    返回 CascadeDecision：
      - level ∈ {L0, L1, L2}：doc_type/confidence 已定案，llm_result=None；
      - level == L3：llm_result 为单次抽取产物（None = LLM 失败，调用方降级
        规则链 fallback——与既有 metadata_llm 失败语义一致）。
    """
    from backend.config.rag import (
        METADATA_CASCADE_EMBED_TIMEOUT,
        METADATA_CASCADE_L1_MIN_GAP,
        METADATA_CASCADE_L1_MIN_SIM,
        METADATA_CASCADE_L1_TOP_K,
        METADATA_CASCADE_L2_MIN_GAP,
        METADATA_CASCADE_L2_MIN_SCORE,
    )

    t0 = time.monotonic()
    span_id = None
    if parent_span_id:
        span_id = trace_collector.start_span(
            "metadata_cascade", parent_id=parent_span_id,
            name="Metadata cascade route",
            type="llm", kind=SpanKind.INDEX_METADATA.value,
        )

    def _seal(d: CascadeDecision) -> CascadeDecision:
        d.latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if span_id:
            trace_collector.end_span(span_id, status="success", metrics={
                "level": d.level, "doc_type": d.doc_type,
                "confidence": d.confidence, "latency_ms": d.latency_ms,
            })
        return d

    # ── L0：文件名/路径强先验 ──
    l0 = _l0_strong_prior(filename, file_path)
    if l0:
        l0.evidence["domain"] = _rule_domain(full_text)
        metadata_route_total.labels(level="L0", outcome="hit").inc()
        return _seal(l0)
    metadata_route_total.labels(level="L0", outcome="miss").inc()

    # ── L1：taxonomy 嵌入检索 ──
    ranked: list[tuple[str, float]] | None = None
    if embedding is not None:
        ranked = await _taxonomy_index.classify(
            full_text, embedding, METADATA_CASCADE_EMBED_TIMEOUT)
    if ranked is None:
        metadata_route_total.labels(level="L1", outcome="error").inc()
    else:
        top_type, top_sim = ranked[0]
        second_sim = ranked[1][1] if len(ranked) > 1 else 0.0
        if (top_sim >= METADATA_CASCADE_L1_MIN_SIM
                and (top_sim - second_sim) >= METADATA_CASCADE_L1_MIN_GAP):
            d = CascadeDecision(
                level="L1", doc_type=top_type, confidence=round(top_sim, 2),
                evidence={"top3": [(t, round(s, 3)) for t, s in ranked[:3]],
                          "domain": _rule_domain(full_text)})
            metadata_route_total.labels(level="L1", outcome="hit").inc()
            return _seal(d)
        metadata_route_total.labels(level="L1", outcome="miss").inc()

        # ── L2：候选内词表复核 ──
        candidates = [t for t, _ in ranked[:METADATA_CASCADE_L1_TOP_K]]
        l2 = _l2_aggregate(full_text, candidates,
                           METADATA_CASCADE_L2_MIN_GAP, METADATA_CASCADE_L2_MIN_SCORE)
        if l2:
            l2.evidence["domain"] = _rule_domain(full_text)
            metadata_route_total.labels(level="L2", outcome="hit").inc()
            return _seal(l2)
        metadata_route_total.labels(level="L2", outcome="miss").inc()

    # ── L3：LLM 单次 schema 抽取（hit/error 打点在 metadata_llm 内部）──
    from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
    llm_result = await extract_metadata_llm_async(
        full_text, filename, parent_span_id=parent_span_id)
    d = CascadeDecision(
        level="L3",
        doc_type=(llm_result or {}).get("doc_type", ""),
        confidence=float((llm_result or {}).get("confidence", 0.0)),
        evidence={"candidates": [(t, round(s, 3)) for t, s in (ranked or [])[:3]]},
        llm_result=llm_result,
    )
    return _seal(d)
