"""Prompt change_kind + 命名指针测试（M4 / 台账 D4）

mock DB 风格（与 test_prompt_service.py 同款，无真实 PostgreSQL）：
1. create_draft 的 change_kind：合法值透传、非法值拒绝
2. set_alias 语义：production=委托 publish（发布语义联动 active_version/
   审计/快照）；staging=只动指针+审计；非法 alias 拒绝
3. get_aliases：指针视图读取
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.prompts.service import PromptService


def _mock_repo_session():
    """AsyncSessionLocal 上下文 + PromptRepository 的最小 mock。"""
    repo = MagicMock()
    repo.create_version = AsyncMock(side_effect=lambda pid, tpl, **kw: MagicMock(
        version=7, **({"change_kind": kw.get("change_kind")} if "change_kind" in kw else {})))
    repo.get_by_key = AsyncMock(return_value=MagicMock(id=1, active_version=6))
    repo.get_version = AsyncMock(return_value=MagicMock(
        id=11, version=7, status="passed", template="ok",
        variables=["q"]))
    repo.upsert_alias = AsyncMock(return_value=MagicMock(alias="staging", version=7))
    repo.list_aliases = AsyncMock(return_value=[
        MagicMock(alias="production", version=6),
        MagicMock(alias="staging", version=7),
    ])
    repo.set_active_version = AsyncMock()
    repo.update_version_status = AsyncMock()
    repo.write_audit = AsyncMock()

    session = MagicMock()
    session.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return repo, ctx


class TestCreateDraftChangeKind:
    @pytest.mark.asyncio
    async def test_valid_change_kind_passthrough(self):
        svc = PromptService()
        repo, ctx = _mock_repo_session()
        with patch("backend.prompts.service.AsyncSessionLocal", return_value=ctx), \
             patch("backend.prompts.service.PromptRepository", return_value=repo), \
             patch("backend.prompts.service.PROMPT_REGISTRY", {}):
            result = await svc.create_draft(
                "test.key", "template", change_kind="minor", created_by="op")
        assert result["change_kind"] == "minor"
        # repo 收到的 create_version 带 change_kind
        assert repo.create_version.call_args.kwargs.get("change_kind") == "minor"

    @pytest.mark.asyncio
    async def test_invalid_change_kind_rejected(self):
        svc = PromptService()
        with patch("backend.prompts.service.PROMPT_REGISTRY", {}):
            with pytest.raises(ValueError, match="major/minor/patch"):
                await svc.create_draft("test.key", "t", change_kind="huge")

    @pytest.mark.asyncio
    async def test_change_kind_optional_backward_compatible(self):
        svc = PromptService()
        repo, ctx = _mock_repo_session()
        with patch("backend.prompts.service.AsyncSessionLocal", return_value=ctx), \
             patch("backend.prompts.service.PromptRepository", return_value=repo), \
             patch("backend.prompts.service.PROMPT_REGISTRY", {}):
            result = await svc.create_draft("test.key", "template")
        assert result["change_kind"] is None  # 不传=未标注（存量兼容）


class TestSetAlias:
    @pytest.mark.asyncio
    async def test_invalid_alias_rejected(self):
        svc = PromptService()
        with pytest.raises(ValueError, match="production/staging"):
            await svc.set_alias("test.key", "canary", 7)

    @pytest.mark.asyncio
    async def test_production_delegates_to_publish(self):
        """production 指针切换必须走完整发布语义（不绕过工作流校验）。"""
        svc = PromptService()
        publish_mock = AsyncMock(return_value={
            "active_version": 7, "previous_version": 6})
        with patch.object(svc, "publish", publish_mock):
            result = await svc.set_alias("test.key", "production", 7,
                                         actor="op", role="admin")
        publish_mock.assert_awaited_once_with(
            "test.key", 7, actor="op", role="admin")
        assert result["alias"] == "production" and result["active_version"] == 7

    @pytest.mark.asyncio
    async def test_staging_only_moves_pointer_and_audits(self):
        """staging 只动指针+审计，不触发发布路径（repo.set_active_version 不被调）。"""
        svc = PromptService()
        repo, ctx = _mock_repo_session()
        with patch("backend.prompts.service.AsyncSessionLocal", return_value=ctx), \
             patch("backend.prompts.service.PromptRepository", return_value=repo), \
             patch("backend.prompts.service.PROMPT_REGISTRY", {}):
            result = await svc.set_alias("test.key", "staging", 7, actor="op")
        assert result == {"alias": "staging", "version": 7}
        repo.upsert_alias.assert_awaited_once()
        repo.set_active_version.assert_not_awaited()  # 运行时读路径不动
        audit_args = repo.write_audit.call_args
        assert audit_args.args[1] == "set_alias"
        assert audit_args.kwargs.get("detail") == {"alias": "staging"}

    @pytest.mark.asyncio
    async def test_staging_unknown_version_404(self):
        svc = PromptService()
        repo, ctx = _mock_repo_session()
        repo.get_version = AsyncMock(return_value=None)
        with patch("backend.prompts.service.AsyncSessionLocal", return_value=ctx), \
             patch("backend.prompts.service.PromptRepository", return_value=repo), \
             patch("backend.prompts.service.PROMPT_REGISTRY", {}):
            with pytest.raises(KeyError):
                await svc.set_alias("test.key", "staging", 99)


class TestGetAliases:
    @pytest.mark.asyncio
    async def test_alias_view(self):
        svc = PromptService()
        repo, ctx = _mock_repo_session()
        with patch("backend.prompts.service.AsyncSessionLocal", return_value=ctx), \
             patch("backend.prompts.service.PromptRepository", return_value=repo), \
             patch("backend.prompts.service.PROMPT_REGISTRY", {}):
            aliases = await svc.get_aliases("test.key")
        assert aliases == {"production": 6, "staging": 7}


class TestPublishSyncsProductionAlias:
    @pytest.mark.asyncio
    async def test_publish_upserts_production_pointer(self):
        """发布必须同步 production 指针（active_version 单一事实 + 命名视图）。"""
        svc = PromptService()
        repo, ctx = _mock_repo_session()
        renderer = MagicMock()
        renderer.validate.return_value = []
        with patch("backend.prompts.service.AsyncSessionLocal", return_value=ctx), \
             patch("backend.prompts.service.PromptRepository", return_value=repo), \
             patch("backend.prompts.service.PROMPT_REGISTRY", {}), \
             patch.object(svc, "_renderer", renderer), \
             patch.object(svc, "_fire_hooks"):
            result = await svc.publish("test.key", 7, actor="op")
        assert result["active_version"] == 7
        repo.set_active_version.assert_awaited_once_with(1, 7)
        repo.upsert_alias.assert_awaited_once_with(1, "production", 7, updated_by="op")
