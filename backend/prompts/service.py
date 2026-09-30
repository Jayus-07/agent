"""PromptService — centralized prompt management.

Read path (3-tier fallback):
  in-process snapshot → DB (via cache) → YAML defaults

Write path:
  create_draft → publish (validate → activate → cache bust → fire hooks) → rollback

Sync path:
  render_sync() uses snapshot only (never touches DB) — for sync call sites
"""
from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass
from typing import Callable

from backend.infra.cache import get_cache
from backend.memory.database import AsyncSessionLocal
from backend.memory.repository.prompt_repo import PromptRepository
from backend.observability.prompt_trace import record_prompt_version
from backend.prompts.registry import PROMPT_REGISTRY, PromptSpec
from backend.prompts.renderer import PromptRenderer, RenderResult
from backend.prompts.workflow import validate_transition
from backend.shared.logger import logger

_prompt_usage_var: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "_prompt_usage_var", default=None,
)


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
        try:
            from backend.prompts.loader import load_defaults
            self._defaults = load_defaults()
        except Exception as exc:
            logger.warning(f"[PromptService] Eager defaults load failed: {exc}")

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
            if not self._snapshot:
                logger.info(
                    f"[PromptService] Snapshot 为空：DB 中无已发布 prompt — "
                    f"正常初始状态，渲染回退 YAML defaults ({len(self._defaults)} 个内置键)"
                )
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

    @staticmethod
    def _record_usage(key: str, version: int | None, source: str) -> None:
        usage = _prompt_usage_var.get()
        if usage is None:
            usage = []
            _prompt_usage_var.set(usage)
        usage.append({"key": key, "version": version, "source": source})

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
            # STOP B4：DB 无行/无 active 版本而回落 defaults 必须显式暴露，
            # 禁止静默 fallback（2026-09-28 prompts 表缺失导致全站回退
            # yaml 而无人察觉的事故教训）。
            logger.warning(
                f"[PromptService] prompt_fallback=true key={key} source=default"
                "（DB 无该 prompt 或无 active 版本，回退内置 defaults）"
            )
            return default, None, "default"

        raise KeyError(f"Prompt not found: {key}")

    async def render(self, key: str, **variables: str) -> RenderResult:
        template, version, source = await self.get_active(key)
        spec = PROMPT_REGISTRY.get(key)
        text = self._renderer.render(template, variables, spec=spec)
        self._record_usage(key, version, source)
        record_prompt_version(key, version, source)
        return RenderResult(text=text, key=key, version=version, source=source)

    def render_sync(self, key: str, **variables: str) -> RenderResult:
        with self._snapshot_lock:
            entry = self._snapshot.get(key)

        if entry:
            spec = PROMPT_REGISTRY.get(key)
            text = self._renderer.render(entry.template, variables, spec=spec)
            self._record_usage(key, entry.version, "snapshot")
            record_prompt_version(key, entry.version, "snapshot")
            return RenderResult(text=text, key=key, version=entry.version, source="snapshot")

        default = self._defaults.get(key)
        if default is not None:
            spec = PROMPT_REGISTRY.get(key)
            text = self._renderer.render(default, variables, spec=spec)
            self._record_usage(key, None, "default")
            record_prompt_version(key, None, "default")
            return RenderResult(text=text, key=key, version=None, source="default")

        raise KeyError(f"Prompt not found in snapshot or defaults: {key}")

    def get_template_sync(self, key: str) -> str:
        """返回原始模板文本（不渲染变量）。

        用于构建 LangChain ChatPromptTemplate 等需要保留 {variable} 占位符的场景。
        """
        with self._snapshot_lock:
            entry = self._snapshot.get(key)
        if entry:
            return entry.template
        default = self._defaults.get(key)
        if default is not None:
            return default
        raise KeyError(f"Prompt not found in snapshot or defaults: {key}")

    def get_version_for_cache_key(self, key: str) -> int | None:
        with self._snapshot_lock:
            entry = self._snapshot.get(key)
            return entry.version if entry else None

    def current_versions(self) -> dict[str, int]:
        """进程内快照的全量 prompt 版本（纯内存零 IO，请求入口 pin 用）。

        M4/#5：runner/task_executor 在请求开始时调用，把结果写进
        AgentState.prompt_versions 与 trace.tags——保证 in-flight 流程
        的版本可追溯（快照语义：后续发布不影响已记录的值）。
        """
        with self._snapshot_lock:
            return {k: e.version for k, e in self._snapshot.items()}

    async def create_draft(
        self,
        key: str,
        template: str,
        *,
        change_kind: str | None = None,
        change_note: str = "",
        created_by: str = "system",
    ) -> dict:
        spec = PROMPT_REGISTRY.get(key)
        if spec and spec.code_controlled:
            raise ValueError(f"Prompt {key} is code-controlled and cannot be modified")
        if change_kind is not None and change_kind not in ("major", "minor", "patch"):
            raise ValueError(
                f"change_kind 必须是 major/minor/patch，实际: {change_kind!r}"
            )

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
                change_kind=change_kind,
                change_note=change_note,
                created_by=created_by,
            )
            await repo.write_audit(
                key, "create_draft",
                to_version=ver.version,
                actor=created_by,
                detail={"template_length": len(template),
                        "change_kind": change_kind},
            )
            await session.commit()
            return {"version": ver.version, "status": "draft", "change_kind": change_kind}

    async def publish(
        self,
        key: str,
        version: int,
        *,
        actor: str = "system",
        role: str = "",
        skip_workflow: bool = False,
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

            if not skip_workflow and ver.status not in ("passed", "published"):
                raise ValueError(
                    f"Version {version} has status '{ver.status}'; "
                    f"only 'passed' or 'published' versions can be published"
                )

            errors = self._renderer.validate(ver.template, spec) if spec else []
            if errors:
                raise ValueError(f"Validation failed: {'; '.join(errors)}")

            old_version = prompt.active_version
            await repo.set_active_version(prompt.id, version)
            # M4：production 指针与 active_version 保持同步（单一事实=active_version，
            # alias 是它的命名视图；切 production=set_alias 的发布路径落到这里）
            await repo.upsert_alias(prompt.id, "production", version, updated_by=actor)
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
        return await self.publish(key, version, actor=actor, role=role, skip_workflow=True)

    async def set_alias(
        self,
        key: str,
        alias: str,
        version: int,
        *,
        actor: str = "system",
        role: str = "",
    ) -> dict:
        """M4（台账 D4）：切换命名指针。

        production：完整发布语义（复用 publish——工作流校验/snapshot 刷新/
          cache 失效/reload 钩子/审计/active_version 同步全部生效）。
        staging：只动指针 + 审计，不影响运行时读路径（读仍走 active_version；
          staging 的运行时消费属 Phase 2 灰度）。
        """
        if alias not in ("production", "staging"):
            raise ValueError(f"alias 必须是 production/staging，实际: {alias!r}")

        if alias == "production":
            result = await self.publish(key, version, actor=actor, role=role)
            result["alias"] = "production"
            return result

        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            prompt = await repo.get_by_key(key)
            if not prompt:
                raise KeyError(f"Prompt not found: {key}")
            ver = await repo.get_version(prompt.id, version)
            if not ver:
                raise KeyError(f"Version {version} not found for {key}")
            await repo.upsert_alias(prompt.id, alias, version, updated_by=actor)
            await repo.write_audit(
                key, "set_alias",
                to_version=version,
                actor=actor, role=role,
                detail={"alias": alias},
            )
            await session.commit()
        return {"alias": alias, "version": version}

    async def get_aliases(self, key: str) -> dict[str, int]:
        """读当前命名指针（无指针的 alias 不出现在结果里）。"""
        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            prompt = await repo.get_by_key(key)
            if not prompt:
                raise KeyError(f"Prompt not found: {key}")
            return {a.alias: a.version for a in await repo.list_aliases(prompt.id)}

    async def transition_status(
        self,
        key: str,
        version: int,
        target_status: str,
        *,
        actor: str = "system",
        role: str = "",
    ) -> dict:
        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            prompt = await repo.get_by_key(key)
            if not prompt:
                raise KeyError(f"Prompt not found: {key}")

            ver = await repo.get_version(prompt.id, version)
            if not ver:
                raise KeyError(f"Version {version} not found for {key}")

            validate_transition(ver.status, target_status)

            await repo.update_version_status(ver.id, target_status)
            await repo.write_audit(
                key, "transition",
                from_version=version,
                to_version=version,
                actor=actor,
                role=role,
                detail={"from_status": ver.status, "to_status": target_status},
            )
            await session.commit()
            return {"version": version, "status": target_status}

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


def collect_prompt_usage() -> list[dict]:
    """Drain the context-local prompt usage log and return it.

    Called by tracer.finish() to attach prompt versions to trace metadata.
    """
    usage = _prompt_usage_var.get()
    if not usage:
        return []
    _prompt_usage_var.set(None)
    return list(usage)


def snapshot_prompt_versions() -> dict[str, int | None]:
    """Return {key: version} for all prompts currently in the snapshot.

    Used by evaluation baseline to record which prompt versions produced the scores.
    """
    with prompt_service._snapshot_lock:
        return {k: e.version for k, e in prompt_service._snapshot.items()}
