"""关键词规则动态管理 — 持久化 + 热加载（PG 实现）。

替代 config/rag.py 中写死的 DEFAULT_KEYWORDS / SIGNAL_RULES。
config 中的值作为初始种子数据，首次启动自动导入。
2026-09-17 SQLite 轨已删除，唯一实现为 PostgresKeywordRuleStore
（keyword_store_pg.py）；本模块保留缓存逻辑与种子映射。
"""
from __future__ import annotations

import time


class KeywordRuleStore:
    """关键词规则持久化存储接口（唯一实现：PostgresKeywordRuleStore）。

    缓存策略: 读取时 60s 内命中缓存，超时从 DB 刷新。
    """

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.rag.preprocessing.keyword_store_pg import PostgresKeywordRuleStore
        return super().__new__(PostgresKeywordRuleStore)
    _CACHE_TTL = 60  # 秒

    # 种子数据 → doc_type 分配规则
    _SEED_DOC_TYPE_MAP = {
        "faq":         ["退货", "差评", "投诉", "faq", "售后", "保修", "索赔", "review", "FAQ", "常见问题", "售后流程", "物流时效", "退货政策", "退换", "客户", "买家", "好评", "Feedback", "AZ", "Chargeback"],
        "product_spec":["SKU", "SPU", "Listing", "上架", "下架", "变体", "品类", "类目", "品牌", "规格", "条码", "标题", "五点", "A+", "主图", "附图", "产品规格", "材质说明", "使用手册", "保养指南", "故障排查", "关键词策略", "搜索词", "排名", "BSR", "BestSeller"],
        "policy":      ["制度", "规范", "审批", "规定", "管理条例", "关键词", "否定词", "匹配类型", "归因", "预算", "广告政策", "投放规则", "竞价策略", "广告规范"],
        "compliance":  ["合规", "法规", "监管", "GDPR", "CCPA", "数据保护", "个人信息", "隐私政策"],
        "legal":       ["合同", "条款", "违约责任", "赔偿", "知识产权", "保密协议", "法律"],
    }

    # ── 查询（带缓存）──

    def get_rules_by_doc_type(self) -> dict[str, list[tuple[str, int]]]:
        """返回 {doc_type: [(keyword, weight), ...]}，60s 缓存。

        与 get_keywords_for_doc_type 不同：这里保留每条词的权重，
        供分类器 classify_with_confidence 按权重累加到类型得分。
        'general' 桶的通用词不返回（分类按类型归因，通用词不偏向任何类型）。
        """
        active = self.get_active()
        return active.get("by_doc_type_w", {})

    def get_active(self) -> dict:
        """返回 {"keywords": [...], "by_doc_type": {...}, "signal_rules": {...}}，60s 缓存"""
        now = time.time()
        if self._cache is not None and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache
        return self._refresh_cache()

    def get_keywords_for_doc_type(self, doc_type: str) -> list[str]:
        """只返回指定文档类型的关键词（用于按类型提取）"""
        active = self.get_active()
        by_dt = active.get("by_doc_type", {})
        general = by_dt.get("general", [])
        specific = by_dt.get(doc_type, [])
        return specific + general  # 通用词兜底

    # ── CRUD ──

# 模块级单例
_store: KeywordRuleStore | None = None


def get_keyword_store(db_path: str | None = None) -> KeywordRuleStore:
    """存储工厂（2026-09-17 SQLite 轨删除，直连 PG 实现）。db_path 参数保留兼容旧签名。"""
    global _store
    if _store is None:
        from backend.rag.preprocessing.keyword_store_pg import PostgresKeywordRuleStore
        _store = PostgresKeywordRuleStore(db_path)
    return _store
