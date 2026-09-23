"""RAG 记忆身份贯通（2026-09-23 D1-7 回归）。

修复前 /rag/ask 链路的 memory 调用全部落在默认 user_id="default"——
所有认证用户读写同一个 L3 记忆空间，A 的事实会注入 B 的 prompt。
锁定新契约：

- pipeline._prepare_context 把调用方声明的 user_id/tenant_id 回填到
  请求级 identity 实例；
- chain._prepare / _remember_turn 把该身份传给 MemoryManager 的
  start_session / end_turn（不再回落 "default"）；
- repo 层 user_id 过滤使 A/B 记忆物理隔离（真实 PG）。
"""
import uuid

import pytest
from sqlalchemy import text

from backend.core.request_context import RequestContext
from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import EMBEDDING_DIM, MemoryRecord
from backend.rag.chain import RAGChain
from backend.rag.context import RagRequestState, clear_context, set_context
from backend.rag.pipeline import RAGPipeline

pytestmark = pytest.mark.asyncio


class _MemoryRecorder:
    def __init__(self):
        self.starts = []
        self.ends = []

    def start_session(self, session_id, question, user_id="default"):
        self.starts.append((session_id, question, user_id))

    def end_turn(self, session_id, question, answer, user_id="default"):
        self.ends.append((session_id, question, answer, user_id))


def _chain_with_identity(user_id: str):
    chain = RAGChain.__new__(RAGChain)
    chain._memory = _MemoryRecorder()
    set_context(RagRequestState(
        metadata_filter={}, intent_label="", query="q",
        identity=RequestContext(user_id=user_id)))
    return chain


def test_authenticated_user_flows_into_memory_calls():
    chain = _chain_with_identity("alice")
    chain._prepare("问题", "s-1")
    chain._remember_turn("s-1", "问题", "回答")
    assert chain._memory.starts == [("s-1", "问题", "alice")]
    assert chain._memory.ends == [("s-1", "问题", "回答", "alice")]


def test_empty_identity_keeps_legacy_default():
    """直调/eval 无身份路径：保留 "default" 兜底（显式契约，非静默串号）。"""
    chain = _chain_with_identity("")
    chain._prepare("问题", "s-1")
    chain._remember_turn("s-1", "问题", "回答")
    assert chain._memory.starts[0][2] == "default"
    assert chain._memory.ends[0][3] == "default"


def test_pipeline_prepare_context_backfills_identity(monkeypatch):
    """pipeline 把调用方声明的身份回填到请求级 identity 实例。"""
    clear_context()
    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe._seen_sessions = {}

    class _StubRouter:
        def route(self, question):
            return {"candidates": []}

    # KBRouter 在 _prepare_context 内函数级 import → 在源模块打桩
    monkeypatch.setattr("backend.rag.routing.kb_router.KBRouter",
                        _StubRouter)
    pipe._prepare_context("default", "测试问题", user_id="bob",
                          tenant_id="t-1")
    from backend.rag.context import get_context
    ident = get_context().identity
    assert ident.user_id == "bob"
    assert ident.tenant_id == "t-1"


async def test_repo_user_isolation_physical(tmp_path):
    """真实 PG：A 写入的事实对 B 的检索物理不可见（user_id 过滤）。"""
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过隔离真实验收: {exc}")

    dim = EMBEDDING_DIM
    vec_a = [1.0] + [0.0] * (dim - 1)
    user_a, user_b = f"iso7-a-{uuid.uuid4().hex[:6]}", f"iso7-b-{uuid.uuid4().hex[:6]}"
    try:
        async with AsyncSessionLocal() as db:
            db.add(MemoryRecord(
                user_id=user_a, session_id="iso7", memory_type="fact",
                content="用户A的部署密钥是 alpha-123",
                embedding=vec_a))
            await db.commit()

        async with AsyncSessionLocal() as db:
            from backend.memory.repository.memory_repo import MemoryRepository
            repo = MemoryRepository(db)
            hits_a = await repo.search_hybrid(vec_a, user_a, top_k=10)
            hits_b = await repo.search_hybrid(vec_a, user_b, top_k=10)

        assert any("alpha-123" in h.content for h in hits_a)
        assert hits_b == []
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(text(
                "DELETE FROM memory_records WHERE user_id IN (:ua, :ub)"),
                {"ua": user_a, "ub": user_b})
            await db.commit()
