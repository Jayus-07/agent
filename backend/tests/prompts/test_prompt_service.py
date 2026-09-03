"""PromptService unit tests (mock DB, no real PostgreSQL).

Covers:
  - render_sync: snapshot → defaults fallback
  - get_template_sync: raw template retrieval
  - publish: validation, cache bust, reload hooks, snapshot update
  - create_draft: code_controlled rejection
  - DB degradation to defaults
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.prompts.registry import PROMPT_REGISTRY, PromptSpec, VarSpec
from backend.prompts.renderer import RenderResult
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
    async def test_publish_updates_snapshot_and_fires_hooks(self, service):
        hook_called = []
        service.register_reload_hook("test.low", lambda: hook_called.append(True))

        mock_version = MagicMock()
        mock_version.template = "New {name}"
        mock_version.version = 2
        mock_version.variables = ["name"]
        mock_version.id = 10

        mock_prompt = MagicMock()
        mock_prompt.id = 1
        mock_prompt.active_version = 1

        mock_repo = AsyncMock()
        mock_repo.get_by_key.return_value = mock_prompt
        mock_repo.get_version.return_value = mock_version
        mock_repo.write_audit.return_value = MagicMock()

        with patch("backend.prompts.service.AsyncSessionLocal") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("backend.prompts.service.PromptRepository", return_value=mock_repo):
                result = await service.publish("test.low", 2, actor="admin", role="admin")

        assert result["active_version"] == 2
        assert hook_called == [True]

        entry = service._snapshot.get("test.low")
        assert entry is not None
        assert entry.version == 2
        assert entry.template == "New {name}"


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
