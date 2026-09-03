"""PromptService — centralized prompt management.

Read path (3-tier fallback):
  in-process snapshot → DB (via cache) → YAML defaults

Write path:
  create_draft → publish (validate → activate → cache bust → fire hooks) → rollback

Sync path:
  render_sync() uses snapshot only (never touches DB) — for sync call sites
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from backend.infra.cache import get_cache
from backend.memory.database import AsyncSessionLocal
from backend.memory.repository.prompt_repo import PromptRepository
from backend.prompts.registry import PROMPT_REGISTRY, PromptSpec
from backend.prompts.renderer import PromptRenderer, PromptRenderError, RenderResult
from backend.shared.logger import logger


@dataclass
class _SnapshotEntry:
    template: str
    version: int
    variables: list


ReloadHook = Callable[[], None]


class PromptService:
    def __init__(self):
        self._renderer = PromptRenderer()
        self._cache = get_cache("prompts", ttl=300)
        self._snapshot: dict[str, _SnapshotEntry] = {}
        self._snapshot_lock = threading.RLock()
        self._reload_hooks: dict[str, list[ReloadHook]] = {}
        self._defaults: dict[str, str] = {}
        self._epoch = 0

    def get_spec(self, key: str) -> PromptSpec | None:
        return PROMPT_REGISTRY.get(key)

    def list_specs(self) -> dict[str, PromptSpec]:
        return dict(PROMPT_REGISTRY)

    async def refresh_snapshot(self) -> None:
        try:
            async with AsyncSessionLocal() as session:
                repo = PromptRepository(session)
                prompts = await repo.list_all()
                with self._snapshot_lock:
                    for p in prompts:
                        if p.active_version is not None:
                            ver = await repo.get_version(p.id, p.active_version)
                            if ver:
                                self._snapshot[p.key] = _SnapshotEntry(
                                    template=ver.template,
                                    version=ver.version,
                                    variables=list(ver.variables) if ver.variables else [],
                                )
                    self._epoch += 1
            logger.info(f"[PromptService] Snapshot refreshed: {len(self._snapshot)} prompts, epoch={self._epoch}")
        except Exception as exc:
            logger.warning(f"[PromptService] Snapshot refresh failed (will use defaults): {exc}")

    def register_reload_hook(self, key: str, callback: ReloadHook) -> None:
        self._reload_hooks.setdefault(key, []).append(callback)

    def _fire_hooks(self, key: str) -> None:
        for cb in self._reload_hooks.get(key, []):
            try:
                cb()
            except Exception as exc:
                logger.warning(f"[PromptService] Reload hook failed for {key}: {exc}")

    async def get_active(self, key: str) -> tuple[str, int | None, str]:
        with self._snapshot_lock:
            entry = self._snapshot.get(key)
            if entry:
                return entry.template, entry.version, "snapshot"

        cached = self._cache.get_json(f"prompt:{key}")
        if cached:
            return cached["template"], cached["version"], "db"

        try:
            async with AsyncSessionLocal() as session:
                repo = PromptRepository(session)
                prompt = await repo.get_by_key(key)
                if prompt and prompt.active_version is not None:
                    ver = await repo.get_version(prompt.id, prompt.active_version)
                    if ver:
                        data = {"template": ver.template, "version": ver.version}
                        self._cache.set_json(f"prompt:{key}", data)
                        with self._snapshot_lock:
                            self._snapshot[key] = _SnapshotEntry(
                                template=ver.template,
                                version=ver.version,
                                variables=list(ver.variables) if ver.variables else [],
                            )
                        return ver.template, ver.version, "db"
        except Exception as exc:
            logger.warning(f"[PromptService] DB lookup failed for {key}: {exc}")

        default = self._defaults.get(key)
        if default is not None:
            return default, None, "default"

        raise KeyError(f"Prompt not found: {key}")

    async def render(self, key: str, **variables: str) -> RenderResult:
        template, version, source = await self.get_active(key)
        spec = PROMPT_REGISTRY.get(key)
        text = self._renderer.render(template, variables, spec=spec)
        return RenderResult(text=text, key=key, version=version, source=source)

    def render_sync(self, key: str, **variables: str) -> RenderResult:
        with self._snapshot_lock:
            entry = self._snapshot.get(key)

        if entry:
            spec = PROMPT_REGISTRY.get(key)
            text = self._renderer.render(entry.template, variables, spec=spec)
            return RenderResult(text=text, key=key, version=entry.version, source="snapshot")

        default = self._defaults.get(key)
        if default is not None:
            spec = PROMPT_REGISTRY.get(key)
            text = self._renderer.render(default, variables, spec=spec)
            return RenderResult(text=text, key=key, version=None, source="default")

        raise KeyError(f"Prompt not found in snapshot or defaults: {key}")

    def get_version_for_cache_key(self, key: str) -> int | None:
        with self._snapshot_lock:
            entry = self._snapshot.get(key)
            return entry.version if entry else None

    async def create_draft(
        self,
        key: str,
        template: str,
        *,
        change_note: str = "",
        created_by: str = "system",
    ) -> dict:
        spec = PROMPT_REGISTRY.get(key)
        if spec and spec.code_controlled:
            raise ValueError(f"Prompt {key} is code-controlled and cannot be modified")

        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            prompt = await repo.get_by_key(key)
            if not prompt:
                raise KeyError(f"Prompt not found: {key}")

            ver = await repo.create_version(
                prompt.id,
                template,
                variables=[v.name for v in spec.variables] if spec else [],
                status="draft",
                change_note=change_note,
                created_by=created_by,
            )
            await repo.write_audit(
                key, "create_draft",
                to_version=ver.version,
                actor=created_by,
                detail={"template_length": len(template)},
            )
            await session.commit()
            return {"version": ver.version, "status": "draft"}

    async def publish(
        self,
        key: str,
        version: int,
        *,
        actor: str = "system",
        role: str = "",
    ) -> dict:
        spec = PROMPT_REGISTRY.get(key)
        if spec and spec.code_controlled:
            raise ValueError(f"Prompt {key} is code-controlled and cannot be published")

        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            prompt = await repo.get_by_key(key)
            if not prompt:
                raise KeyError(f"Prompt not found: {key}")

            ver = await repo.get_version(prompt.id, version)
            if not ver:
                raise KeyError(f"Version {version} not found for {key}")

            errors = self._renderer.validate(ver.template, spec) if spec else []
            if errors:
                raise ValueError(f"Validation failed: {'; '.join(errors)}")

            old_version = prompt.active_version
            await repo.set_active_version(prompt.id, version)
            await repo.update_version_status(ver.id, "published")
            self._cache.delete(f"prompt:{key}")

            with self._snapshot_lock:
                self._snapshot[key] = _SnapshotEntry(
                    template=ver.template,
                    version=ver.version,
                    variables=list(ver.variables) if ver.variables else [],
                )
                self._epoch += 1

            await repo.write_audit(
                key, "publish",
                from_version=old_version,
                to_version=version,
                actor=actor,
                role=role,
            )
            await session.commit()

        self._fire_hooks(key)
        return {"active_version": version, "previous_version": old_version}

    async def rollback(
        self,
        key: str,
        version: int,
        *,
        actor: str = "system",
        role: str = "",
    ) -> dict:
        return await self.publish(key, version, actor=actor, role=role)

    async def seed_defaults(self, defaults: dict[str, str]) -> dict:
        count = 0
        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            for key, template in defaults.items():
                spec = PROMPT_REGISTRY.get(key)
                if not spec:
                    continue
                if spec.code_controlled:
                    continue

                prompt = await repo.upsert_prompt(
                    key=key,
                    name=spec.name,
                    category=spec.category,
                    risk_level=spec.risk_level,
                    variables=[v.name for v in spec.variables],
                    is_code_controlled=False,
                )
                existing = await repo.get_version(prompt.id, 1)
                if not existing:
                    await repo.create_version(
                        prompt.id,
                        template,
                        variables=[v.name for v in spec.variables],
                        status="published",
                        change_note="Initial seed from YAML defaults",
                        created_by="seed",
                    )
                    await repo.set_active_version(prompt.id, 1)
                    await repo.write_audit(
                        key, "seed",
                        to_version=1,
                        actor="seed",
                        detail={"source": "yaml_default"},
                    )
                    count += 1

            await session.commit()

        await self.refresh_snapshot()
        return {"seeded": count}

    def load_defaults_into_memory(self, defaults: dict[str, str]) -> None:
        self._defaults.update(defaults)

    @property
    def epoch(self) -> int:
        return self._epoch


prompt_service = PromptService()
