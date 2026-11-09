"""dreaming 整合任务回归（ADR-0012 第 2 期 D4，D-2/D-4 裁决形态）。

锁定语义：
- 开关默认关 → skipped 不动库（与 eval 采样同保守模式）；
- 高相似同义碎片 → LLM merge → 聚合行 + 碎片 supersede 版本链（失效双写）；
- LLM 判矛盾 → 指定行失效（invalid_at，无 supersede 回填）；
- 安全关键词行豁免；簇内含 explicit 整体跳过；
- beat 注册 + 路由登记（fail-closed 面）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.tests.memory.conftest import require_memory_pg


def _require_infra() -> None:
    require_memory_pg()
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='memory_records' AND column_name='invalid_at'"
            )
            if not cursor.fetchall():
                pytest.skip("145 迁移未应用（invalid_at 缺失），跳过")


@pytest.fixture()
def _isolated_user():
    _require_infra()
    user = f"dream-{uuid.uuid4().hex[:10]}"
    tenant = "tenant-dream"
    yield user, tenant
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM memory_records WHERE user_id LIKE %s",
                           (f"{user}%",))
        conn.commit()


class _FakeEmbedding:
    def embed_query(self, text: str) -> list[float]:
        import hashlib

        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        return [(seed >> i) & 1 for i in range(1024)]


def _enable_dreaming(monkeypatch):
    import backend.memory.dreaming as dreaming_mod

    monkeypatch.setattr(dreaming_mod, "MEMORY_DREAMING_ENABLED", True)


def _stub_llm(monkeypatch, payload: dict):
    import json

    from backend.memory.dreaming import MemoryDreamingService

    async def _fake_call(self, prompt: str) -> str:
        return json.dumps(payload)

    monkeypatch.setattr(MemoryDreamingService, "_call_llm", _fake_call)


def _frag(content: str, user: str, tenant: str, *, origin: str = "inferred",
          mtype: str = "preference", embedding: list[float] | None = None) -> dict:
    """embedding=None 时用确定性伪随机向量（不同文本 ~0.5 不成簇）；
    需要成簇的用例显式传同一 embedding（sim=1.0 稳定）——伪随机向量
    不保语义相似，语义相似度由生产 embedding 服务保证。"""
    return dict(
        tenant_id=tenant, product="agent", user_id=user, session_id="",
        memory_type=mtype, content=content,
        embedding=embedding if embedding is not None
        else _FakeEmbedding().embed_query(content),
        importance_score=0.5, confidence_score=0.7, origin=origin,
        verification_status="verified", scope="user_global",
    )


async def _insert_fragments(user: str, tenant: str,
                            frags: list[dict]) -> None:
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.models.memory import MemoryRecord
    from backend.memory.repository.memory_repo import MemoryRepository

    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for f in frags:
            await repo.insert(MemoryRecord(**f))
        await db.commit()


async def _rows(user: str) -> list[tuple]:
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT content, is_active, invalid_at, superseded_by, "
                "verification_status FROM memory_records "
                "WHERE user_id=%s ORDER BY created_at", (user,))
            return cursor.fetchall()


class TestDreaming:
    def test_disabled_returns_skipped(self, _isolated_user, monkeypatch):
        """开关默认关：skipped 正常终态，不动库。"""
        from backend.memory.dreaming import MemoryDreamingService

        user, tenant = _isolated_user
        _enable_dreaming_off = None  # 保持默认 False
        asyncio.run(_insert_fragments(user, tenant, [
            _frag(f"{user} 偏好简洁报表", user, tenant),
            _frag(f"{user} 喜欢简洁的报表风格", user, tenant),
        ]))

        result = asyncio.run(MemoryDreamingService().run(user_scope=(tenant, "agent", user)))
        assert result["status"] == "skipped"
        rows = asyncio.run(_rows(user))
        assert all(r[1] is True for r in rows), "关闭状态下不得有任何动作"

    def test_merge_supersedes_fragments(self, _isolated_user, monkeypatch):
        """同义碎片合并：聚合行 pending 新行 + 碎片失效双写 + 版本链指向。"""
        from backend.memory.dreaming import MemoryDreamingService

        user, tenant = _isolated_user
        _enable_dreaming(monkeypatch)
        _stub_llm(monkeypatch, {"action": "merge",
                                "merged": f"{user} 偏好简洁的报表风格"})
        emb = _FakeEmbedding().embed_query("cluster")
        asyncio.run(_insert_fragments(user, tenant, [
            _frag(f"{user} 偏好简洁报表", user, tenant, embedding=emb),
            _frag(f"{user} 喜欢简洁的报表风格", user, tenant, embedding=emb),
        ]))

        result = asyncio.run(MemoryDreamingService().run(user_scope=(tenant, "agent", user)))
        assert result["merged"] == 1, result

        rows = asyncio.run(_rows(user))
        assert len(rows) == 3, "应存在 2 碎片 + 1 聚合行"
        merged_rows = [r for r in rows if r[4] == "pending"]
        assert len(merged_rows) == 1, "聚合行为 pending（机器产物不直进召回）"
        fragments = [r for r in rows if r[4] == "verified"]
        assert len(fragments) == 2
        for content, active, invalid_at, superseded_by, _vs in fragments:
            assert active is False, f"碎片未失效: {content}"
            assert invalid_at is not None, "碎片失效时刻未标记（D3 双写）"
            assert superseded_by is not None, "碎片版本链未回填"
        assert merged_rows[0][1] is True and merged_rows[0][2] is None

    def test_contradiction_invalidates_old_row(self, _isolated_user, monkeypatch):
        """LLM 判矛盾：指定行失效（invalid_at 双写，无 supersede 回填）。"""
        from backend.memory.dreaming import MemoryDreamingService

        user, tenant = _isolated_user
        _enable_dreaming(monkeypatch)
        _stub_llm(monkeypatch, {"action": "contradiction", "superseded": [0]})
        emb = _FakeEmbedding().embed_query("cluster")
        asyncio.run(_insert_fragments(user, tenant, [
            _frag(f"{user} 常用微信支付", user, tenant, embedding=emb),
            _frag(f"{user} 现在只用支付宝支付", user, tenant, embedding=emb),
        ]))

        result = asyncio.run(MemoryDreamingService().run(user_scope=(tenant, "agent", user)))
        assert result["contradictions"] == 1, result

        rows = asyncio.run(_rows(user))
        assert len(rows) == 2, "矛盾失效不产生新行"
        invalidated = rows[0]
        kept = rows[1]
        assert invalidated[1] is False and invalidated[2] is not None
        assert invalidated[3] is None, "矛盾失效无取代者，不得回填 superseded_by"
        assert kept[1] is True and kept[2] is None

    def test_safety_content_exempt(self, _isolated_user, monkeypatch):
        """安全关键词行豁免：不参与合并/失效（宁宽勿严）。"""
        from backend.memory.dreaming import MemoryDreamingService

        user, tenant = _isolated_user
        _enable_dreaming(monkeypatch)
        _stub_llm(monkeypatch, {"action": "merge", "merged": "merged"})
        asyncio.run(_insert_fragments(user, tenant, [
            _frag(f"{user} 对花生过敏", user, tenant),
            _frag(f"{user} 花生过敏要避开", user, tenant),
        ]))

        result = asyncio.run(MemoryDreamingService().run(user_scope=(tenant, "agent", user)))
        assert result["merged"] == 0 and result["clusters_examined"] == 0, result

        rows = asyncio.run(_rows(user))
        assert all(r[1] is True for r in rows), "安全行不得被失效"

    def test_explicit_cluster_skipped(self, _isolated_user, monkeypatch):
        """簇内含 explicit → 整体跳过（用户显式资产不做机器改写）。"""
        from backend.memory.dreaming import MemoryDreamingService

        user, tenant = _isolated_user
        _enable_dreaming(monkeypatch)
        _stub_llm(monkeypatch, {"action": "merge", "merged": "merged"})
        emb = _FakeEmbedding().embed_query("cluster")
        asyncio.run(_insert_fragments(user, tenant, [
            _frag(f"{user} 偏好简洁报表", user, tenant, origin="explicit",
                  embedding=emb),
            _frag(f"{user} 喜欢简洁的报表风格", user, tenant, embedding=emb),
        ]))

        result = asyncio.run(MemoryDreamingService().run(user_scope=(tenant, "agent", user)))
        assert result["merged"] == 0 and result["skipped_clusters"] == 1, result

    def test_beat_registered(self):
        """task + schedule + 路由登记（fail-closed 面，decay 测试同款）。"""
        import backend.tasks.memory_maintenance_tasks  # noqa: F401
        from backend.tasks.celery_app import celery_app
        from backend.tasks.queue_router import beat_queue

        assert "memory.dreaming" in celery_app.tasks
        entry = celery_app.conf.beat_schedule.get("memory-dreaming")
        assert entry is not None and entry["task"] == "memory.dreaming"
        assert beat_queue("memory.dreaming") == entry["options"]["queue"]
