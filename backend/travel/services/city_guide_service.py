"""travel/services/city_guide_service.py — 城市指南（M3-g，四级内容链）

用户拍板的内容优先级（docs/travel-rag-doc-spec.md 配套）：
  ① travel 库已上传文档的 LLM 摘要（最高可信，带文档出处，零额外成本）
  ② RAG chunk 检索补细节（美食/避雷，retrieve_knowledge 语料提炼）
  ③ 知乎搜索（RAG 覆盖不足才触发，标「AI 生成」，出处照挂）
  ④ 「暂无指南，问助手」引导

检索双过滤（拍板口径）：kb_id=travel AND doc_type=travel_guide——
不读其他库、不读其他类型。

缓存：生成结果按目的地缓存 TTL 7 天（拍板口径：同城二次打开秒出，
不烧配额）。知乎关闭/配额尽 → 自动降级本地 RAG → 「暂无」。

本服务**不进旅游域图**（域图为行程规划设计，城市指南是独立轻功能），
由 HTTP 轻端点直调；规划流程内的城市速览卡复用同一服务的缓存写入。
"""
from __future__ import annotations

from backend.shared.logger import logger

_GUIDE_CACHE_TTL = 7 * 24 * 3600  # 拍板：TTL 7 天


def _pipeline():
    from backend.rag.pipeline import get_rag_pipeline

    return get_rag_pipeline()


def _travel_retrieve(query: str, top_k: int = 4) -> list[str]:
    """RAG 语料检索（双过滤 + 显式系统主体）。

    retrieve_knowledge 只收 kb_id；doc_type 过滤由 metadata_filter 承担——
    pipeline 侧 QueryAnalyzer 未见 doc_type 参数，这里用 kb 内检索后按
    chunk 元数据 section 过滤不可行（轻量检索不回元数据），因此 doc_type
    约束落在**上传侧**（travel 库只灌 travel_guide 语料，见文档规范），
    检索侧以 kb_id=travel 为边界。语料治理纪律写进文档规范 §一。
    """
    from backend.config.travel import TRAVEL_RAG_KB_ID, TRAVEL_RAG_TOP_K

    try:
        text = _pipeline().retrieve_knowledge(
            query,
            kb_id=TRAVEL_RAG_KB_ID,
            top_k=top_k or TRAVEL_RAG_TOP_K,
            system_subject="travel_city_guide",
        )
    except Exception as e:  # noqa: BLE001 — RAG 是增强项，失败降级
        logger.warning("[CityGuide] RAG 检索失败（降级）: %s", e)
        return []
    chunks = [c.strip() for c in (text or "").split("\n") if len(c.strip()) > 8]
    return chunks[:top_k]


def _doc_summary(destination: str) -> dict | None:
    """① travel 库文档摘要（按 destination 匹配的 travel_guide 文档）。

    摘要来自上传链路 LLM 富化（metadata.summary，≤300 字），直接展示
    无需再调 LLM。匹配口径：doc_registry 按 kb=travel + 标题/元数据含
    目的地名；实现走 registry 查询，失败返回 None（继续下一级）。
    """
    try:
        from backend.rag.indexing.doc_registry_pg import PostgresDocumentRegistry
        from backend.config.travel import TRAVEL_RAG_KB_ID

        registry = PostgresDocumentRegistry()
        docs = registry.list_by_kb(TRAVEL_RAG_KB_ID) or []
        # 双过滤 + 目的地匹配：doc_type=travel_guide（上传侧治理，文档规范 §一）
        # 且 文件名/元数据 destination 命中；summary 直接消费（上传链路已生成）
        for doc in docs:
            if str(doc.get("doc_type") or "") != "travel_guide":
                continue
            meta = {}
            try:
                import json as _json
                meta = _json.loads(doc.get("metadata") or "{}") if isinstance(doc.get("metadata"), str) else (doc.get("metadata") or {})
            except Exception:  # noqa: BLE001
                meta = {}
            dest = str(meta.get("destination") or "")
            fname = str(doc.get("file_name") or doc.get("file_path") or "")
            if dest != destination and destination not in fname:
                continue
            summary = str(doc.get("summary") or "")
            if summary:
                return {
                    "title": fname or f"{destination}旅游攻略",
                    "doc_id": str(doc.get("doc_id") or ""),
                    "summary": summary,
                }
    except Exception as e:  # noqa: BLE001 — ①级失败降级，不阻塞
        logger.warning("[CityGuide] 文档摘要获取失败（降级）: %s", e)
    return None


def _zhihu_guides(destination: str) -> list[dict]:
    """③ 知乎攻略（三主题合一，经缓存层）。"""
    from backend.travel.services.live_search_service import (
        guides_preview, search_zhihu_guides,
    )

    merged: list[dict] = []
    for topic in ("city", "attraction", "food"):
        try:
            data = search_zhihu_guides(destination=destination, limit=4, topic=topic)
            preview = guides_preview(data, "知乎 · 攻略", topic=topic)
            merged.extend(preview.get("preview") or [])
        except Exception as e:  # noqa: BLE001 — 单主题失败不影响其余
            logger.warning("[CityGuide] 知乎检索失败 topic=%s: %s", topic, e)
    # 去重（标题级）
    seen: set[str] = set()
    unique: list[dict] = []
    for item in merged:
        title = str(item.get("title") or "")
        if title and title not in seen:
            seen.add(title)
            unique.append(item)
    return unique[:6]


def get_city_guide(destination: str, *, force: bool = False) -> dict:
    """城市指南主入口：缓存 → 四级内容链。

    Returns:
        {
          destination, source_level: 1|2|3|4,
          summary: {title, doc_id, text} | None,     # ①级
          tips: [str],                                # ②级语料提炼（避雷/贴士）
          guides: [知乎卡],                           # ③级（source_level>=3 时）
          status: ok | empty, note: str,
        }
    """
    destination = (destination or "").strip()
    if not destination:
        return {"destination": "", "source_level": 4, "status": "empty",
                "note": "未指定目的地", "summary": None, "tips": [], "guides": []}

    # 缓存（TTL 7 天，拍板口径；force 跳过读仍写）
    # v2：level 聚合修复（初始 4 恒 4 的旧结构缓存用 key 版本位自然淘汰）
    cache_key = f"cityguide:v2:{destination}"
    if not force:
        try:
            from backend.infra.cache.backend import get_cache

            cached = get_cache("travel_city_guide", ttl=_GUIDE_CACHE_TTL).get_json(cache_key)
            if cached:
                logger.info("[CityGuide] 缓存命中 %s", destination)
                return cached
        except Exception as e:  # noqa: BLE001
            logger.warning("[CityGuide] 缓存读取失败: %s", e)

    # source_level 聚合初始 0（未知）；各级命中取 max 升档，全空落 4
    guide: dict = {
        "destination": destination,
        "source_level": 0,
        "status": "empty",
        "note": "",
        "summary": None,
        "tips": [],
        "guides": [],
    }

    # ① 文档摘要
    summary = _doc_summary(destination)
    if summary:
        guide["source_level"] = 1
        guide["status"] = "ok"
        guide["summary"] = summary

    # ② RAG 语料提炼（避雷/贴士向）
    tips = _travel_retrieve(f"{destination} 避雷 注意事项 贴士", top_k=4)
    if tips:
        guide["source_level"] = max(guide["source_level"], 2)
        guide["status"] = "ok"
        guide["tips"] = tips

    # ③ 知乎（RAG 覆盖不足才触发：①② 都空，或仅 ① 无 ②）
    if not tips:
        try:
            from backend.config.travel import TRAVEL_GUIDE_ZHIHU_ENABLED

            if TRAVEL_GUIDE_ZHIHU_ENABLED:
                guides = _zhihu_guides(destination)
                if guides:
                    guide["source_level"] = 3
                    guide["status"] = "ok"
                    guide["guides"] = guides
                    guide["note"] = "内容来自知乎攻略（AI 汇总，仅供参考）"
        except Exception as e:  # noqa: BLE001 — 知乎关闭/配额尽降级
            logger.warning("[CityGuide] 知乎降级: %s", e)

    if guide["status"] == "empty":
        guide["source_level"] = 4
        guide["note"] = "暂无该城市的指南内容；可以直接问右侧助手"

    # 写缓存（即便 empty 也缓存短周期内避免反复烧知乎——TTL 同 7 天，
    # force/新语料上传后可用 force 刷新）
    try:
        from backend.infra.cache.backend import get_cache

        get_cache("travel_city_guide", ttl=_GUIDE_CACHE_TTL).set_json(cache_key, guide, ttl=_GUIDE_CACHE_TTL)
    except Exception as e:  # noqa: BLE001
        logger.debug("[CityGuide] 缓存写入失败: %s", e)
    return guide


def prime_city_guide(destination: str) -> dict:
    """规划流程内预热：城市速览卡消费同一入口，结果写缓存（fire-and-forget 语义由调用方决定）。"""
    return get_city_guide(destination)
