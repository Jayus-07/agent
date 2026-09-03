"""PromptRepository — async CRUD for prompts / prompt_versions / prompt_audit_log"""
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.memory.models.prompt import Prompt, PromptAuditLog, PromptVersion


class PromptRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get_by_key(self, key: str) -> Prompt | None:
        result = await self._s.execute(
            select(Prompt).where(Prompt.key == key)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        category: str | None = None,
        risk_level: str | None = None,
        q: str | None = None,
    ) -> list[Prompt]:
        stmt = select(Prompt)
        if category:
            stmt = stmt.where(Prompt.category == category)
        if risk_level:
            stmt = stmt.where(Prompt.risk_level == risk_level)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                Prompt.key.ilike(pattern) | Prompt.name.ilike(pattern)
            )
        stmt = stmt.order_by(Prompt.key)
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def upsert_prompt(
        self,
        key: str,
        *,
        name: str = "",
        description: str = "",
        category: str = "",
        risk_level: str | None = None,
        template_engine: str = "str_format",
        variables: list | None = None,
        is_code_controlled: bool = False,
    ) -> Prompt:
        existing = await self.get_by_key(key)
        if existing:
            existing.name = name or existing.name
            existing.description = description or existing.description
            existing.category = category or existing.category
            if risk_level is not None:
                existing.risk_level = risk_level
            existing.template_engine = template_engine
            if variables is not None:
                existing.variables = variables
            existing.is_code_controlled = is_code_controlled
            existing.updated_at = datetime.now(timezone.utc)
            await self._s.flush()
            return existing

        prompt = Prompt(
            key=key,
            name=name,
            description=description,
            category=category,
            risk_level=risk_level or "low",
            template_engine=template_engine,
            variables=variables or [],
            is_code_controlled=is_code_controlled,
        )
        self._s.add(prompt)
        await self._s.flush()
        return prompt

    async def create_version(
        self,
        prompt_id: int,
        template: str,
        *,
        variables: list | None = None,
        status: str = "draft",
        change_note: str = "",
        created_by: str = "system",
    ) -> PromptVersion:
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": prompt_id},
        )
        result = await self._s.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) + 1 "
                "FROM prompt_versions WHERE prompt_id = :pid"
            ),
            {"pid": prompt_id},
        )
        next_version = result.scalar()

        ver = PromptVersion(
            prompt_id=prompt_id,
            version=next_version,
            template=template,
            variables=variables or [],
            status=status,
            change_note=change_note,
            created_by=created_by,
        )
        self._s.add(ver)
        await self._s.flush()
        return ver

    async def get_version(self, prompt_id: int, version: int) -> PromptVersion | None:
        result = await self._s.execute(
            select(PromptVersion).where(
                PromptVersion.prompt_id == prompt_id,
                PromptVersion.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def list_versions(self, prompt_id: int) -> list[PromptVersion]:
        result = await self._s.execute(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version.desc())
        )
        return list(result.scalars().all())

    async def set_active_version(self, prompt_id: int, version: int) -> None:
        await self._s.execute(
            update(Prompt)
            .where(Prompt.id == prompt_id)
            .values(active_version=version, updated_at=datetime.now(timezone.utc))
        )
        await self._s.flush()

    async def update_version_status(
        self, version_id: int, status: str
    ) -> None:
        await self._s.execute(
            update(PromptVersion)
            .where(PromptVersion.id == version_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await self._s.flush()

    async def get_latest_versions(
        self, prompt_ids: list[int]
    ) -> dict[int, PromptVersion]:
        if not prompt_ids:
            return {}
        stmt = (
            select(PromptVersion)
            .distinct(PromptVersion.prompt_id)
            .where(PromptVersion.prompt_id.in_(prompt_ids))
            .order_by(PromptVersion.prompt_id, PromptVersion.version.desc())
        )
        result = await self._s.execute(stmt)
        rows = result.scalars().all()
        return {r.prompt_id: r for r in rows}

    async def write_audit(
        self,
        prompt_key: str,
        action: str,
        *,
        from_version: int | None = None,
        to_version: int | None = None,
        actor: str = "system",
        role: str = "",
        detail: dict | None = None,
    ) -> PromptAuditLog:
        log = PromptAuditLog(
            prompt_key=prompt_key,
            action=action,
            from_version=from_version,
            to_version=to_version,
            actor=actor,
            role=role,
            detail=detail,
        )
        self._s.add(log)
        await self._s.flush()
        return log

    async def list_audit(
        self, prompt_key: str, limit: int = 50
    ) -> list[PromptAuditLog]:
        result = await self._s.execute(
            select(PromptAuditLog)
            .where(PromptAuditLog.prompt_key == prompt_key)
            .order_by(PromptAuditLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
