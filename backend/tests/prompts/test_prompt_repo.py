"""PromptRepository integration tests (requires PostgreSQL).

Marked with @pytest.mark.pg so they can be skipped in unit-only runs.
"""
import pytest
import pytest_asyncio

from backend.memory.models.prompt import Prompt, PromptVersion, PromptAuditLog


pytestmark = pytest.mark.pg


@pytest_asyncio.fixture
async def repo_session():
    """Create an async session with the prompt tables."""
    import backend.memory.database as db_mod
    from backend.memory.models.prompt import Base

    db_mod._engine = None
    db_mod._sessionmaker = None

    await db_mod._ensure_engine()
    async with db_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db_mod.AsyncSessionLocal() as session:
        yield session

    async with db_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db_mod._engine.dispose()
    db_mod._engine = None
    db_mod._sessionmaker = None


@pytest_asyncio.fixture
async def repo(repo_session):
    from backend.memory.repository.prompt_repo import PromptRepository
    return PromptRepository(repo_session)


@pytest.mark.asyncio
async def test_upsert_and_get(repo):
    p = await repo.upsert_prompt(
        key="test.integration",
        name="Integration Test",
        category="test",
        risk_level="low",
    )
    assert p.id is not None
    assert p.key == "test.integration"

    fetched = await repo.get_by_key("test.integration")
    assert fetched is not None
    assert fetched.name == "Integration Test"


@pytest.mark.asyncio
async def test_create_version_increments(repo):
    p = await repo.upsert_prompt(key="test.ver", name="Ver Test", category="test")
    v1 = await repo.create_version(p.id, "Template v1", status="published")
    v2 = await repo.create_version(p.id, "Template v2", status="draft")
    assert v1.version == 1
    assert v2.version == 2


@pytest.mark.asyncio
async def test_set_active_version(repo):
    p = await repo.upsert_prompt(key="test.active", name="Active Test", category="test")
    v1 = await repo.create_version(p.id, "T1", status="published")
    await repo.set_active_version(p.id, 1)

    fetched = await repo.get_by_key("test.active")
    assert fetched.active_version == 1


@pytest.mark.asyncio
async def test_list_versions_ordered(repo):
    p = await repo.upsert_prompt(key="test.list", name="List Test", category="test")
    await repo.create_version(p.id, "V1")
    await repo.create_version(p.id, "V2")
    await repo.create_version(p.id, "V3")

    versions = await repo.list_versions(p.id)
    assert [v.version for v in versions] == [3, 2, 1]


@pytest.mark.asyncio
async def test_write_and_list_audit(repo):
    await repo.upsert_prompt(key="test.audit", name="Audit Test", category="test")
    await repo.write_audit("test.audit", "seed", to_version=1, actor="test")
    await repo.write_audit("test.audit", "publish", from_version=1, to_version=2, actor="admin")

    logs = await repo.list_audit("test.audit")
    assert len(logs) == 2
    actions = {log.action for log in logs}
    assert actions == {"seed", "publish"}


@pytest.mark.asyncio
async def test_list_all_with_filters(repo):
    await repo.upsert_prompt(key="test.a", name="A", category="rag", risk_level="low")
    await repo.upsert_prompt(key="test.b", name="B", category="sql", risk_level="high")

    all_prompts = await repo.list_all()
    assert len(all_prompts) >= 2

    rag_only = await repo.list_all(category="rag")
    assert all(p.category == "rag" for p in rag_only)

    high_only = await repo.list_all(risk_level="high")
    assert all(p.risk_level == "high" for p in high_only)

    search = await repo.list_all(q="test.a")
    assert any(p.key == "test.a" for p in search)
