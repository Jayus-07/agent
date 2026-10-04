"""tools/travel/knowledge.py — 旅游域知识库检索（P0-1 RAG 接入）

把既有 RAG Pipeline（pgvector + BM25 + rerank）作为旅游域的**事实补充源**：
签证/文化/安全/攻略这类「不适合硬编码进种子数据、但行程单应该引用」的知识。

设计纪律（与 risk 专家同一立场）：
  - **检索失败必须降级而不是报错**：RAG 是增强项，检索挂了行程照出，
    只是在行程单里如实说明「知识库暂不可用」。
  - **只取原文不生成**：用 retrieve_knowledge（BM25→向量→rerank，~3-5s）
    而不是 ask()（完整 LLM 链路）—— risk 专家要的是可引用的原文摘录，
    不是又一轮模型输出；也避免在域图内再嵌一次 LLM 调用。
  - **kb_id 可配置**：默认指向独立旅游库（TRAVEL_RAG_KB_ID=travel），
    语料未灌入时检索为空 → 自动退回免责声明，不报错不空转。

并发考量：pipeline 单例内部已有缓存与连接池；本模块不做额外缓存，
超时/异常全部软失败 —— 天气与知识检索都不应成为排程链路的新瓶颈。
"""
from __future__ import annotations

import re
from datetime import date

from backend.config import travel as T
from backend.shared.logger import logger


def retrieve_travel_knowledge(query: str) -> tuple[list[str], str]:
    """检索旅游知识库，返回 (原文摘录列表, 来源标识)。

    检索为空或失败返回 ([], "")，调用方据此降级。
    """
    if not T.TRAVEL_RAG_ENABLED:
        return [], ""

    try:
        from backend.rag.pipeline import get_rag_pipeline

        pipeline = get_rag_pipeline()
        text = pipeline.retrieve_knowledge(
            query,
            kb_id=T.TRAVEL_RAG_KB_ID,
            top_k=T.TRAVEL_RAG_TOP_K,
            system_subject="travel_domain",
        )
    except Exception as e:  # noqa: BLE001 — 检索失败不阻塞排程
        logger.warning("[TravelKnowledge] 知识库检索失败（降级跳过）: %s", e)
        return [], ""

    chunks = [c.strip() for c in (text or "").split("\n") if c.strip()]
    if not chunks:
        return [], ""
    return (annotate_stale_years(chunks[:T.TRAVEL_RAG_TOP_K]),
            f"rag:{T.TRAVEL_RAG_KB_ID}")


# 文内年份探测（验收 #94 文内证据口径）：RAG pipeline 只回纯文本（文档
# 发布/爬取时间断在 RAG 元数据层，未透传到本线），但攻略正文常自带年份——
# 摘录含「去年及更早」的年份时如实标注「可能较旧」。只声明文内证据，
# 不冒充对整份攻略新鲜度的判定；未来 RAG 线把 published_at 透传后，
# 本函数应替换为元数据口径（替换点唯一在此）。
_STALE_YEAR_RE = re.compile(r"(20\d{2})\s*年")


def annotate_stale_years(chunks: list[str], today: date | None = None) -> list[str]:
    """摘录文内年份早于去年 → 追加「（文中提及 YYYY 年，可能较旧）」。"""
    today = today or date.today()
    stale_before = today.year - 1
    out: list[str] = []
    for chunk in chunks:
        years = [int(y) for y in _STALE_YEAR_RE.findall(chunk or "")]
        oldest = min(years) if years else None
        if oldest is not None and oldest <= stale_before:
            out.append(f"{chunk}（文中提及 {oldest} 年，可能较旧）")
        else:
            out.append(chunk)
    return out


def build_knowledge_query(destination: str, preferences: list[str]) -> str:
    """构造检索问句（纯函数）：目的地 + 偏好决定检索侧重。"""
    focus = "、".join(preferences[:3]) if preferences else "综合攻略"
    return (f"{destination} 旅游 注意事项 安全 签证 攻略 {focus}").strip()
