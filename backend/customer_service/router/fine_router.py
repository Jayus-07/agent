"""customer_service/router/fine_router.py — 客服细分类路由

按域分流:
  - KNOWLEDGE / COMPLAINT / HUMAN: 规则直接映射（关键词 → 意图）
  - TRANSACTION / AFTER_SALES / ACCOUNT: 向量 + LLM fallback
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDomain
from backend.shared.logger import logger

# ── 规则意图映射（K / C / H 域用）──
_RULE_INTENT_MAP: dict[str, dict[str, list[str]]] = {
    "KNOWLEDGE": {
        "k_policy": ["政策", "退货政策", "保修", "条款", "退换条件"],
        "k_product": ["产品", "规格", "参数", "怎么用", "使用说明"],
        "k_promotion": ["活动", "优惠", "促销", "满减", "优惠券", "折扣"],
        "k_warranty": ["保修期", "延保", "保修卡", "保修范围"],
        "k_faq": ["营业时间", "支付方式", "发票", "配送范围"],
    },
    "COMPLAINT": {
        "c_complaint": ["投诉", "举报", "不满意", "差评", "说法", "维权"],
        "c_feedback": ["建议", "反馈", "改进", "希望"],
    },
    "HUMAN": {
        "h_handoff": ["转人工", "真人", "人工客服", "人工服务", "不要机器人"],
        "h_supervisor": ["领导", "经理", "主管", "负责人"],
    },
}


class CSFineRouter:
    """客服细分类 — 输出 intent 字符串。"""

    def __init__(self):
        self._collection = None
        self._ensure_index()

    def _ensure_index(self) -> None:
        try:
            from pathlib import Path

            from langchain_chroma import Chroma

            from backend.config.customer_service import CS_ROUTER_INDEX_DIR
            from backend.customer_service.router.seeds import CS_FINE_SEEDS
            from backend.rag.embedding_singleton import get_embedding

            Path(CS_ROUTER_INDEX_DIR).mkdir(parents=True, exist_ok=True)
            embedding = get_embedding()
            self._collection = Chroma(
                collection_name="cs_fine_v1",
                embedding_function=embedding,
                persist_directory=CS_ROUTER_INDEX_DIR,
            )

            if self._collection._collection.count() == 0:
                texts, metadatas = [], []
                for intent_id, seeds in CS_FINE_SEEDS.items():
                    for s in seeds:
                        texts.append(s)
                        metadatas.append({"intent": intent_id})
                if texts:
                    self._collection.add_texts(texts=texts, metadatas=metadatas)
                    logger.info(f"[CSFineRouter] 已建细分类索引: {len(texts)} 条种子")
        except Exception as e:
            logger.warning(f"[CSFineRouter] 索引初始化失败: {e}")
            self._collection = None

    def classify(self, query: str, domain: CSDomain) -> tuple[str, float, str]:
        """细分类入口。

        Returns:
            (intent, confidence, reason)
        """
        domain_val = domain.value

        if domain_val in _RULE_INTENT_MAP:
            intent, conf, reason = self._rule_classify(query, domain_val)
            if conf >= 0.7:
                return intent, conf, f"rule: {reason}"

        intent_v, conf_v, reason_v = self._vector_classify(query, domain)
        if conf_v >= 0.6:
            return intent_v, conf_v, f"vector: {reason_v}"

        from backend.customer_service.router.intents import DOMAIN_DEFAULT_INTENT
        default = DOMAIN_DEFAULT_INTENT.get(domain_val, "k_faq")
        return default, 0.3, f"default_fallback: {default}"

    def _rule_classify(self, query: str, domain: str) -> tuple[str, float, str]:
        intent_map = _RULE_INTENT_MAP.get(domain, {})
        best_intent, best_count = "k_faq", 0
        for intent, keywords in intent_map.items():
            count = sum(1 for kw in keywords if kw in query)
            if count > best_count:
                best_intent, best_count = intent, count

        if best_count >= 1:
            conf = min(best_count / 2.0, 1.0)
            return best_intent, conf, f"{best_intent}({best_count}hits)"
        return "k_faq", 0.0, ""

    def _vector_classify(self, query: str, domain: CSDomain) -> tuple[str, float, str]:
        if self._collection is None or self._collection._collection.count() == 0:
            return "k_faq", 0.0, "no_index"
        try:
            from backend.customer_service.router.intents import INTENT_PROFILES

            domain_intents = [
                k for k, v in INTENT_PROFILES.items() if v.domain == domain
            ]

            results = self._collection.similarity_search_with_score(query, k=5)
            if not results:
                return "k_faq", 0.0, "no_results"

            intent_scores: dict[str, list[float]] = {}
            for doc, distance in results:
                intent_id = doc.metadata.get("intent", "")
                if intent_id not in domain_intents:
                    continue
                score = 1.0 / (1.0 + distance)
                intent_scores.setdefault(intent_id, []).append(score)

            best_intent, best_score = "k_faq", 0.0
            for intent_id, scores in intent_scores.items():
                avg = sum(scores) / len(scores)
                if avg > best_score:
                    best_intent, best_score = intent_id, avg

            if best_score > 0:
                return best_intent, best_score, f"top={best_intent}({best_score:.2f})"
            return "k_faq", 0.0, "no_domain_match"
        except Exception as e:
            logger.debug(f"[CSFineRouter] 向量检索失败: {e}")
            return "k_faq", 0.0, "error"
