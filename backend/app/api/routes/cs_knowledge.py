"""cs_knowledge.py — 知识助手（cs-workstation-v2 P7）

  - GET  /cs/knowledge/unresolved        未解决问题聚合（7 日窗口，按归一化哈希聚类）
  - POST /cs/knowledge/faq-draft         由未解决问题生成 FAQ 草稿（status=draft，
                                          幂等 faq_key；进既有 cs_faq 草稿→发布机制）
  - GET  /cs/knowledge/search?q=         知识检索（FAQ 只读命中 + 置信分，零台账）
  - 话术管理复用 P4 /cs/canned-responses CRUD（个人/团队两级，零新增）

数据面：
  未解决聚合 = ai.unanswered_questions（拒答/空结果旁路台账，有租户与
  question_hash 聚类口径，管理端 /admin/unanswered/* 同源）。原型「低置信」
  徽章对应真实数据类别 sql_empty（前端显示「查询空结果」，TASK 已回写）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)

router = APIRouter(tags=["智能客服-知识助手"])

_SOURCE_ZH = {"rag_miss": "拒答", "sql_empty": "查询空结果"}


class QaFaqDraftBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    variants: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("question", "variants")
    @classmethod
    def _not_blank(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("问题不能为空白")
            return v
        cleaned = [item.strip() for item in v if item and item.strip()]
        if not cleaned and v:
            raise ValueError("变体不能全为空白")
        return cleaned


class UnresolvedItem(BaseModel):
    question: str
    variants: dict[str, int]
    hits: int
    last_seen: str | None = None
    source: str
    source_display: str


class UnresolvedResponse(BaseModel):
    items: list[UnresolvedItem]
    total: int


class FaqDraftResponse(BaseModel):
    faq_id: int
    faq_key: str
    status: str


class SearchItem(BaseModel):
    question: str
    answer: str
    score: float
    matched: bool


class SearchResponse(BaseModel):
    items: list[SearchItem]
    query: str


@router.get("/cs/knowledge/unresolved", response_model=UnresolvedResponse)
async def unresolved_questions(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> UnresolvedResponse:
    """未解决问题聚合（按 question_hash 聚类，含原始查询变体分布）。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    params = {"tenant": context.tenant_id, "since": since, "limit": limit}
    rows = (
        await session.execute(text("""
            SELECT u.question_hash,
                   (array_agg(u.question ORDER BY u.created_at DESC))[1]
                       AS latest_question,
                   count(*)                                              AS hits,
                   max(u.created_at)                                     AS last_seen,
                   (array_agg(u.source ORDER BY u.created_at DESC))[1]   AS source,
                   (SELECT jsonb_object_agg(v.question, v.cnt)
                      FROM (SELECT u2.question, count(*) AS cnt
                              FROM ai.unanswered_questions u2
                             WHERE u2.tenant_id = u.tenant_id
                               AND u2.question_hash = u.question_hash
                               AND u2.created_at >= :since
                             GROUP BY u2.question
                             ORDER BY cnt DESC) v)                     AS variants
            FROM ai.unanswered_questions u
            WHERE u.tenant_id = :tenant AND u.created_at >= :since
            GROUP BY u.question_hash, u.tenant_id
            ORDER BY hits DESC
            LIMIT :limit
        """), params)
    ).mappings().all()
    items = [
        UnresolvedItem(
            question=row["latest_question"],
            variants=row["variants"] or {},
            hits=int(row["hits"] or 0),
            last_seen=row["last_seen"].isoformat() if row["last_seen"] else None,
            source=str(row["source"] or "rag_miss"),
            source_display=_SOURCE_ZH.get(
                str(row["source"] or "rag_miss"), "拒答",
            ),
        )
        for row in rows
    ]
    return UnresolvedResponse(items=items, total=len(items))


@router.post("/cs/knowledge/faq-draft", response_model=FaqDraftResponse)
async def create_faq_draft(
    body: QaFaqDraftBody,
    context: AgentContext = Depends(require_agent),
) -> FaqDraftResponse:
    """生成 FAQ 草稿：写 ai.cs_faq status=draft（faq_key 幂等，不重复建行）。

    草稿不进线上（load_published 只取 published）；答案留占位由知识运营
    在管理端补全后发布——即「缺口 → 草稿 → 审核」闭环的入口。
    """
    from backend.customer_service.faq import get_faq_store

    variants = [v for v in body.variants if v != body.question]
    faq_id = get_faq_store().upsert_faq(
        body.question,
        "（草稿）待知识运营补充答案。",
        variants=variants,
        status="draft",
        kb_refs=f"cs-knowledge-gap:{context.tenant_id}",
    )
    from backend.customer_service.faq import normalize_question

    return FaqDraftResponse(
        faq_id=faq_id,
        faq_key=normalize_question(body.question),
        status="draft",
    )


@router.get("/cs/knowledge/search", response_model=SearchResponse)
async def knowledge_search(
    q: str = Query(min_length=1, max_length=500),
    context: AgentContext = Depends(require_agent),
) -> SearchResponse:
    """知识检索：FAQ 只读命中（精确=1.0 / bigram Jaccard 相似度），零台账。"""
    from backend.customer_service.faq import get_faq_store

    query = q.strip()
    if not query:
        raise HTTPException(422, detail="检索词不能为空")
    results = get_faq_store().search(query, k=5, min_score=0.30)
    return SearchResponse(
        items=[
            SearchItem(
                question=item["question"],
                answer=item["answer"],
                score=item["score"],
                matched=item["matched"],
            )
            for item in results
        ],
        query=query,
    )
