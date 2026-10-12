"""PromptService unit tests (mock DB, no real PostgreSQL).

Covers:
  - render_sync: snapshot → defaults fallback
  - get_template_sync: raw template retrieval
  - publish: validation, cache bust, reload hooks, snapshot update
  - create_draft: code_controlled rejection
  - DB degradation to defaults
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.prompts.service import PromptService, _SnapshotEntry


@pytest.fixture
def service():
    s = PromptService()
    s._defaults = {
        "test.low": "Hello {name}!",
        "test.rag": "<!--META--> Context: {context}\nQuestion: {input}",
    }
    return s


class TestRenderSync:
    def test_from_snapshot(self, service):
        service._snapshot["test.low"] = _SnapshotEntry(
            template="Hi {name}!", version=3, variables=["name"]
        )
        result = service.render_sync("test.low", name="Alice")
        assert result.text == "Hi Alice!"
        assert result.version == 3
        assert result.source == "snapshot"

    def test_from_defaults_fallback(self, service):
        result = service.render_sync("test.low", name="Bob")
        assert result.text == "Hello Bob!"
        assert result.version is None
        assert result.source == "default"

    def test_missing_key_raises(self, service):
        with pytest.raises(KeyError, match="not found"):
            service.render_sync("nonexistent.key")

    def test_snapshot_takes_priority_over_defaults(self, service):
        service._snapshot["test.low"] = _SnapshotEntry(
            template="Override {name}", version=5, variables=["name"]
        )
        result = service.render_sync("test.low", name="X")
        assert result.text == "Override X"
        assert result.source == "snapshot"


class TestGetTemplateSync:
    def test_returns_raw_template(self, service):
        service._snapshot["test.low"] = _SnapshotEntry(
            template="Raw {name} template", version=1, variables=["name"]
        )
        result = service.get_template_sync("test.low")
        assert result == "Raw {name} template"
        assert "{name}" in result

    def test_fallback_to_defaults(self, service):
        result = service.get_template_sync("test.low")
        assert "{name}" in result

    def test_missing_key_raises(self, service):
        with pytest.raises(KeyError):
            service.get_template_sync("nonexistent")


class TestRecordTemplateUsageSync:
    def test_records_snapshot_version(self, service, monkeypatch):
        service._snapshot["test.low"] = _SnapshotEntry(
            template="Raw {name} template", version=7, variables=["name"]
        )
        recorded = []
        monkeypatch.setattr(
            "backend.prompts.service.record_prompt_version",
            lambda key, version, source: recorded.append((key, version, source)),
        )

        result = service.record_template_usage_sync("test.low")

        assert result == (7, "snapshot")
        assert recorded == [("test.low", 7, "snapshot")]

    def test_records_default_usage(self, service, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "backend.prompts.service.record_prompt_version",
            lambda key, version, source: recorded.append((key, version, source)),
        )

        result = service.record_template_usage_sync("test.low")

        assert result == (None, "default")
        assert recorded == [("test.low", None, "default")]


class TestCreateDraft:
    @pytest.mark.asyncio
    async def test_code_controlled_rejected(self, service):
        with pytest.raises(ValueError, match="code-controlled"):
            await service.create_draft("security.input_guard", "evil template")


class TestPublish:
    @pytest.mark.asyncio
    async def test_code_controlled_rejected(self, service):
        with pytest.raises(ValueError, match="code-controlled"):
            await service.publish("security.input_guard", 1)

    @pytest.mark.asyncio
    async def test_release_gate_requires_atomic_release_transition(self, service):
        with pytest.raises(ValueError, match="release 状态迁移"):
            await service.publish(
                "test.low", 2, role="release_gate", skip_workflow=True,
            )

    @pytest.mark.asyncio
    async def test_publish_updates_snapshot_and_fires_hooks(self, service):
        hook_called = []
        calls = []
        service.register_reload_hook("test.low", lambda: hook_called.append(True))

        def fail_cache_invalidation(_key):
            calls.append("cache")
            raise RuntimeError("cache unavailable")

        service._cache.delete = MagicMock(side_effect=fail_cache_invalidation)

        mock_version = MagicMock()
        mock_version.template = "New {name}"
        mock_version.version = 2
        mock_version.variables = ["name"]
        mock_version.id = 10
        # publish 守卫要求版本状态为 passed/published（MagicMock 默认值会被拒绝）
        mock_version.status = "passed"

        mock_prompt = MagicMock()
        mock_prompt.id = 1
        mock_prompt.active_version = 1

        mock_repo = AsyncMock()
        mock_repo.get_by_key.return_value = mock_prompt
        mock_repo.get_version.return_value = mock_version
        mock_repo.write_audit.return_value = MagicMock()

        with patch("backend.prompts.service.AsyncSessionLocal") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.commit = AsyncMock(
                side_effect=lambda: calls.append("commit")
            )
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("backend.prompts.service.PromptRepository", return_value=mock_repo):
                with patch(
                    "backend.prompts.hot_reload.stage_prompt_epoch",
                    new=AsyncMock(return_value=2),
                ) as stage:
                    with patch(
                        "backend.prompts.hot_reload.dispatch_prompt_epoch",
                        new=AsyncMock(
                            side_effect=lambda *_args: calls.append("dispatch")
                        ),
                    ) as dispatch:
                        result = await service.publish(
                            "test.low", 2, actor="admin", role="admin"
                        )

        assert result["active_version"] == 2
        assert hook_called == [True]

        entry = service._snapshot.get("test.low")
        assert entry is not None
        assert entry.version == 2
        assert entry.template == "New {name}"
        assert calls == ["commit", "cache", "dispatch"]
        stage.assert_awaited_once_with(
            mock_session, key="test.low", actor="admin",
        )
        dispatch.assert_awaited_once_with("test.low", "admin", 2)

        recorded = []
        with patch(
            "backend.prompts.service.record_prompt_version",
            side_effect=lambda *args: recorded.append(args),
        ):
            rendered = service.render_sync("test.low", name="after publish")
        assert rendered.version == 2
        assert recorded == [("test.low", 2, "snapshot")]

    @pytest.mark.asyncio
    async def test_commit_failure_preserves_active_snapshot_and_skips_cache_refresh(
        self, service,
    ):
        service._snapshot["test.low"] = _SnapshotEntry(
            template="Old {name}", version=1, variables=["name"],
        )
        service._cache.delete = MagicMock()

        mock_version = MagicMock(
            template="New {name}", version=2, variables=["name"], id=10,
            status="passed",
        )
        mock_prompt = MagicMock(id=1, active_version=1)
        mock_repo = AsyncMock()
        mock_repo.get_by_key.return_value = mock_prompt
        mock_repo.get_version.return_value = mock_version

        with patch("backend.prompts.service.AsyncSessionLocal") as session_cls:
            session = AsyncMock()
            session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
            session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "backend.prompts.service.PromptRepository", return_value=mock_repo,
            ):
                with patch(
                    "backend.prompts.hot_reload.stage_prompt_epoch",
                    new=AsyncMock(return_value=3),
                ):
                    with patch(
                        "backend.prompts.hot_reload.dispatch_prompt_epoch",
                        new=AsyncMock(),
                    ) as dispatch:
                        with pytest.raises(RuntimeError, match="commit failed"):
                            await service.publish("test.low", 2)

        assert service._snapshot["test.low"].version == 1
        assert service._snapshot["test.low"].template == "Old {name}"
        service._cache.delete.assert_not_called()
        dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollback_rejects_unpublished_draft(self, service):
        mock_version = MagicMock(
            template="Draft {name}", version=2, variables=["name"], id=10,
            status="draft",
        )
        mock_prompt = MagicMock(id=1, active_version=1)
        mock_repo = AsyncMock()
        mock_repo.get_by_key.return_value = mock_prompt
        mock_repo.get_version.return_value = mock_version

        with patch("backend.prompts.service.AsyncSessionLocal") as session_cls:
            session = AsyncMock()
            session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "backend.prompts.service.PromptRepository", return_value=mock_repo,
            ):
                with pytest.raises(ValueError, match="操作要求状态 'published' / 'archived'"):
                    await service.rollback("test.low", 2, actor="admin")

        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollback_restores_archived_version_with_publish_audit(self, service):
        archived = MagicMock(
            template="Previously published {name}", version=2,
            variables=["name"], id=10, status="archived",
        )
        mock_prompt = MagicMock(id=1, active_version=3)
        mock_repo = AsyncMock()
        mock_repo.get_by_key.return_value = mock_prompt
        mock_repo.get_version.return_value = archived
        mock_repo.has_publish_audit.return_value = True

        with patch("backend.prompts.service.AsyncSessionLocal") as session_cls:
            session = AsyncMock()
            session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "backend.prompts.service.PromptRepository", return_value=mock_repo,
            ):
                with patch(
                    "backend.prompts.hot_reload.stage_prompt_epoch",
                    new=AsyncMock(return_value=4),
                ):
                    with patch(
                        "backend.prompts.hot_reload.dispatch_prompt_epoch",
                        new=AsyncMock(),
                    ):
                        result = await service.rollback("test.low", 2, actor="admin")

        assert result == {"active_version": 2, "previous_version": 3}
        mock_repo.has_publish_audit.assert_awaited_once_with("test.low", 2)
        mock_repo.set_active_version.assert_awaited_once_with(
            1, 2, expected_current_version=3,
        )

    @pytest.mark.asyncio
    async def test_rollback_rejects_archived_without_publish_audit(self, service):
        archived = MagicMock(
            template="Unpublished {name}", version=2,
            variables=["name"], id=10, status="archived",
        )
        mock_repo = AsyncMock()
        mock_repo.get_by_key.return_value = MagicMock(id=1, active_version=3)
        mock_repo.get_version.return_value = archived
        mock_repo.has_publish_audit.return_value = False

        with patch("backend.prompts.service.AsyncSessionLocal") as session_cls:
            session = AsyncMock()
            session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "backend.prompts.service.PromptRepository", return_value=mock_repo,
            ):
                with pytest.raises(ValueError, match="没有已提交的发布记录"):
                    await service.rollback("test.low", 2, actor="admin")

        mock_repo.set_active_version.assert_not_awaited()
        session.commit.assert_not_awaited()

    def test_older_publish_epoch_cannot_overwrite_newer_snapshot(self, service):
        newer = _SnapshotEntry("New {name}", version=3, variables=["name"])
        older = _SnapshotEntry("Old {name}", version=2, variables=["name"])

        assert service._apply_published_snapshot("test.low", newer, 8)
        assert not service._apply_published_snapshot("test.low", older, 7)
        assert service._snapshot["test.low"].version == 3
        assert service._snapshot_publish_epochs["test.low"] == 8

    @pytest.mark.asyncio
    async def test_concurrent_publish_keeps_commit_order_in_snapshot(self, service):
        state = SimpleNamespace(active_version=1)
        first_commit_applied = asyncio.Event()
        release_first_commit = asyncio.Event()
        second_epoch_staged = asyncio.Event()

        class FakeSession:
            def __init__(self):
                self.pending_version = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def commit(self):
                state.active_version = self.pending_version
                if self.pending_version == 2:
                    first_commit_applied.set()
                    await release_first_commit.wait()

        sessions = []

        def session_factory():
            session = FakeSession()
            sessions.append(session)
            return session

        class FakeRepository:
            def __init__(self, session):
                self.session = session

            async def get_by_key(self, _key):
                return SimpleNamespace(id=1, active_version=state.active_version)

            async def get_version(self, _prompt_id, version):
                return SimpleNamespace(
                    id=version,
                    version=version,
                    template=f"Version {version} {{name}}",
                    variables=["name"],
                    status="passed",
                )

            async def set_active_version(
                self, _prompt_id, version, *, expected_current_version,
            ):
                if state.active_version != expected_current_version:
                    return False
                self.session.pending_version = version
                return True

            async def upsert_alias(self, *_args, **_kwargs):
                return None

            async def update_version_status(self, *_args):
                return None

            async def write_audit(self, *_args, **_kwargs):
                return None

        async def stage_epoch(session, **_kwargs):
            if session.pending_version == 3:
                second_epoch_staged.set()
            return session.pending_version - 1

        service._cache.delete = MagicMock()
        with patch("backend.prompts.service.AsyncSessionLocal", session_factory):
            with patch(
                "backend.prompts.service.PromptRepository", FakeRepository,
            ):
                with patch(
                    "backend.prompts.hot_reload.stage_prompt_epoch",
                    side_effect=stage_epoch,
                ):
                    with patch(
                        "backend.prompts.hot_reload.dispatch_prompt_epoch",
                        new=AsyncMock(),
                    ):
                        first = asyncio.create_task(service.publish("test.low", 2))
                        await first_commit_applied.wait()
                        second = asyncio.create_task(service.publish("test.low", 3))
                        await second_epoch_staged.wait()
                        await asyncio.sleep(0.005)
                        release_first_commit.set()
                        await asyncio.gather(first, second)

        assert state.active_version == 3
        assert service._snapshot["test.low"].version == 3
        assert service._snapshot_publish_epochs["test.low"] == 2
        assert len(sessions) == 2


class TestReloadHooks:
    def test_register_and_fire(self, service):
        results = []
        service.register_reload_hook("k1", lambda: results.append("a"))
        service.register_reload_hook("k1", lambda: results.append("b"))
        service._fire_hooks("k1")
        assert results == ["a", "b"]

    def test_hook_exception_doesnt_propagate(self, service):
        def bad_hook():
            raise RuntimeError("boom")
        service.register_reload_hook("k1", bad_hook)
        service._fire_hooks("k1")

    def test_no_hooks_for_unknown_key(self, service):
        service._fire_hooks("nonexistent")


class TestEpoch:
    def test_epoch_increments_on_refresh(self, service):
        assert service.epoch == 0
        service._epoch += 1
        assert service.epoch == 1


class TestDBDegradation:
    def test_render_sync_uses_defaults_when_no_snapshot(self, service):
        assert len(service._snapshot) == 0
        result = service.render_sync("test.low", name="World")
        assert result.source == "default"
        assert result.text == "Hello World!"


class TestPromptUsageTracking:
    def test_render_sync_records_usage(self, service):
        from backend.prompts.service import _prompt_usage_var, collect_prompt_usage
        _prompt_usage_var.set(None)

        service._snapshot["test.low"] = _SnapshotEntry(
            template="Hi {name}!", version=3, variables=["name"],
        )
        service.render_sync("test.low", name="Alice")

        usage = collect_prompt_usage()
        assert len(usage) == 1
        assert usage[0]["key"] == "test.low"
        assert usage[0]["version"] == 3
        assert usage[0]["source"] == "snapshot"

    def test_render_sync_default_records_none_version(self, service):
        from backend.prompts.service import _prompt_usage_var, collect_prompt_usage
        _prompt_usage_var.set(None)

        service.render_sync("test.low", name="Bob")

        usage = collect_prompt_usage()
        assert len(usage) == 1
        assert usage[0]["version"] is None
        assert usage[0]["source"] == "default"

    def test_collect_drains_usage(self, service):
        from backend.prompts.service import _prompt_usage_var, collect_prompt_usage
        _prompt_usage_var.set(None)

        service.render_sync("test.low", name="A")
        service.render_sync("test.low", name="B")

        first = collect_prompt_usage()
        assert len(first) == 2

        second = collect_prompt_usage()
        assert second == []

    def test_collect_empty_when_no_renders(self):
        from backend.prompts.service import _prompt_usage_var, collect_prompt_usage
        _prompt_usage_var.set(None)
        assert collect_prompt_usage() == []


class TestSnapshotPromptVersions:
    def test_returns_snapshot_versions(self, service):
        from backend.prompts.service import prompt_service, snapshot_prompt_versions
        original_snapshot = prompt_service._snapshot

        try:
            prompt_service._snapshot = {
                "k1": _SnapshotEntry(template="t1", version=1, variables=[]),
                "k2": _SnapshotEntry(template="t2", version=5, variables=[]),
            }
            result = snapshot_prompt_versions()
            assert result == {"k1": 1, "k2": 5}
        finally:
            prompt_service._snapshot = original_snapshot

    def test_empty_snapshot(self):
        from backend.prompts.service import prompt_service, snapshot_prompt_versions
        original_snapshot = prompt_service._snapshot
        try:
            prompt_service._snapshot = {}
            assert snapshot_prompt_versions() == {}
        finally:
            prompt_service._snapshot = original_snapshot
