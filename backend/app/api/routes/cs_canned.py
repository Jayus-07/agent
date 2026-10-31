"""cs_canned.py — 坐席快捷回复话术 CRUD（cs-workstation-v2 P4）

两级作用域：
  - personal：仅 owner 本人可见可改（他人视角 404，与列表口径一致）
  - team：本租户全员可见；仅 supervisor 可建/改/删（403）

事务口径（P3 统一）：route 持有 ``async with session.begin()``；身份反查
require_agent 用独立短会话。全部查询带 tenant_id（租户隔离，跨租户 id 404）。
「存为话术」= POST scope=personal，由 AI 助手面板/输入区调用。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)
from backend.customer_service.models.canned import CSCannedResponse

router = APIRouter(tags=["智能客服-快捷回复话术"])


@dataclass(frozen=True)
class CannedView:
    id: int
    scope: str
    owner_agent_id: str | None
    title: str
    content: str
    category: str
    enabled: bool
    updated_at: datetime | None


class CannedCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="通用", min_length=1, max_length=32)
    scope: str = Field(default="personal", pattern="^(personal|team)$")

    @field_validator("title", "content", "category")
    @classmethod
    def _strip_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空白")
        return v


class CannedUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=32)
    enabled: bool | None = None

    @field_validator("title", "content", "category")
    @classmethod
    def _strip_not_blank(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("不能为空白")
        return v


class CannedItemDTO(BaseModel):
    id: int
    scope: str
    owner_agent_id: str | None
    title: str
    content: str
    category: str
    enabled: bool
    updated_at: str | None


class CannedListResponse(BaseModel):
    items: list[CannedItemDTO]
    total: int


class CannedMutationResponse(BaseModel):
    id: int
    scope: str
    title: str
    content: str
    category: str
    enabled: bool


def _dto(row: CSCannedResponse) -> CannedItemDTO:
    return CannedItemDTO(
        id=row.id,
        scope=row.scope,
        owner_agent_id=row.owner_agent_id,
        title=row.title,
        content=row.content,
        category=row.category,
        enabled=row.enabled,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _require_can_write(context: AgentContext, row: CSCannedResponse) -> None:
    """写权限闸：team 话术仅 supervisor；personal 话术仅本人（他人 404）。"""
    if row.scope == "team":
        if not context.is_supervisor:
            raise HTTPException(403, detail="团队话术仅主管可修改")
        return
    if row.owner_agent_id != context.agent_id:
        # 他人个人话术视同不存在（列表查询也不返回它），防枚举口径一致
        raise HTTPException(404, detail="话术不存在")


async def _load_owned(
    session: AsyncSession, *, tenant_id: str, canned_id: int,
) -> CSCannedResponse:
    row = (
        await session.execute(
            select(CSCannedResponse).where(
                CSCannedResponse.id == canned_id,
                CSCannedResponse.tenant_id == tenant_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail="话术不存在")
    return row


@router.get("/cs/canned-responses", response_model=CannedListResponse)
async def list_canned(
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> CannedListResponse:
    """本人个人话术 + 本租户团队话术（含禁用行，前端据 enabled 置灰）。"""
    rows = (
        await session.execute(
            select(CSCannedResponse)
            .where(
                CSCannedResponse.tenant_id == context.tenant_id,
                (
                    (CSCannedResponse.scope == "team")
                    | (
                        (CSCannedResponse.scope == "personal")
                        & (CSCannedResponse.owner_agent_id == context.agent_id)
                    )
                ),
            )
            .order_by(CSCannedResponse.updated_at.desc())
            .limit(500)
        )
    ).scalars().all()
    items = [_dto(row) for row in rows]
    return CannedListResponse(items=items, total=len(items))


@router.post("/cs/canned-responses", response_model=CannedMutationResponse, status_code=201)
async def create_canned(
    body: CannedCreateBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> CannedMutationResponse:
    """新建话术；scope=team 仅 supervisor（个人/团队两级写权限）。"""
    if body.scope == "team" and not context.is_supervisor:
        raise HTTPException(403, detail="团队话术仅主管可创建")
    async with session.begin():
        row = CSCannedResponse(
            tenant_id=context.tenant_id,
            scope=body.scope,
            owner_agent_id=(
                context.agent_id if body.scope == "personal" else None
            ),
            title=body.title.strip(),
            content=body.content.strip(),
            category=body.category.strip() or "通用",
            enabled=True,
        )
        session.add(row)
        await session.flush()
        return CannedMutationResponse(
            id=row.id,
            scope=row.scope,
            title=row.title,
            content=row.content,
            category=row.category,
            enabled=row.enabled,
        )


@router.patch("/cs/canned-responses/{canned_id}", response_model=CannedMutationResponse)
async def update_canned(
    canned_id: int,
    body: CannedUpdateBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> CannedMutationResponse:
    async with session.begin():
        row = await _load_owned(
            session, tenant_id=context.tenant_id, canned_id=canned_id,
        )
        _require_can_write(context, row)
        if body.title is not None:
            row.title = body.title.strip()
        if body.content is not None:
            row.content = body.content.strip()
        if body.category is not None:
            row.category = body.category.strip() or "通用"
        if body.enabled is not None:
            row.enabled = body.enabled
        await session.flush()
        return CannedMutationResponse(
            id=row.id,
            scope=row.scope,
            title=row.title,
            content=row.content,
            category=row.category,
            enabled=row.enabled,
        )


@router.delete("/cs/canned-responses/{canned_id}", status_code=204)
async def delete_canned(
    canned_id: int,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> None:
    async with session.begin():
        row = await _load_owned(
            session, tenant_id=context.tenant_id, canned_id=canned_id,
        )
        _require_can_write(context, row)
        await session.delete(row)
