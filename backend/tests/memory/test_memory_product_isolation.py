"""记忆产品命名空间隔离回归（ADR-0012 第 1 期，安全审计 #68）。

修复前：RAG 链记忆调用点（chain._prepare/_remember_turn、客服知识服务）
以 raw user_id 直读写 agent 产品记忆命名空间——客服会话历史进入企业助手
上下文，反之亦然。修复后：MemoryService 唯一入口按 (tenant, product, user)
解析命名空间，product 为独立列。

本文件锁定双产品隔离的物理层语义（真实 PG，仿 test_memory_isolation 模式：
直接 repo 层断言，不依赖 LLM 抽取与 embedding 服务的可用性）：
同一租户、同一 raw user、同一 session_id 下，agent 与 customer_service 两
产品的 L2 会话与 L3 长期记忆互不可见。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.tests.memory.conftest import require_memory_pg


def _require_product_column() -> None:
    require_memory_pg()
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='memory_records' AND column_name='product'"
            )
            if not cursor.fetchall():
                pytest.skip("143 迁移未应用（product 列缺失），跳过")


@pytest.fixture()
def _isolated_user():
    require_memory_pg()
    _require_product_column()
    user = f"pt-isol-{uuid.uuid4().hex[:10]}"
    tenant = "tenant-pt-isol"
    yield user, tenant
    # 同步清理（fixture 内不做异步调度）：测试数据按 user/session 前缀删除，
    # 覆盖 memory_records / chat_sessions（chat_messages 随会话级联）。
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM memory_records WHERE user_id LIKE %s",
                           (f"{user}%",))
            cursor.execute("DELETE FROM chat_sessions WHERE session_id LIKE %s",
                           (f"{user}%",))
        conn.commit()


AGENT, CS = "agent", "customer_service"


class TestProductIsolation:
    def test_l3_facts_isolated_by_product(self, _isolated_user):
        """同 user 同 memory_key 在两产品下写入互不覆盖、互不可见（repo 物理层）。"""
        from backend.memory.database import AsyncSessionLocal
        from backend.memory.keying import StoreOutcome
        from backend.memory.long_term import LongTermMemory, MemoryFact
        from backend.memory.repository.memory_repo import MemoryRepository

        user, tenant = _isolated_user
        session = f"{user}-sess"

        async def _scenario():
            async with AsyncSessionLocal() as db:
                repo = MemoryRepository(db)
                l3 = LongTermMemory(repo)
                # 替换 embedding（容器测试无供应商凭据）：按文本确定性伪随机
                # 向量，隔离断言不依赖相似度精度
                import hashlib as _hl

                class _FakeEmbedding:
                    def embed_query(self, text: str) -> list[float]:
                        seed = int(_hl.sha256(text.encode()).hexdigest()[:8], 16)
                        return [(seed >> i) & 1 for i in range(1024)]

                l3._embedding_model = _FakeEmbedding()
                # agent 产品写 keyed 事实
                r1 = await l3.store_with_resolution(
                    MemoryFact(fact_type="preference",
                               content=f"{user} 的报告风格偏好是简洁",
                               session_id=session, origin="explicit",
                               confidence_score=0.9, memory_key="preference.report_style",
                               structured_value="concise"),
                    user, session, tenant, product=AGENT)
                # 客服产品写同 key 不同值 → 不得触发 agent 侧 supersede（隔离）
                r2 = await l3.store_with_resolution(
                    MemoryFact(fact_type="preference",
                               content=f"{user} 的报告风格偏好是详细",
                               session_id=session, origin="explicit",
                               confidence_score=0.9, memory_key="preference.report_style",
                               structured_value="detailed"),
                    user, session, tenant, product=CS)
                assert r1.outcome == StoreOutcome.INSERTED, r1
                assert r2.outcome == StoreOutcome.INSERTED, r2
                # agent 侧按 key 定位：只能看到自己的「简洁」，看不到 CS 的「详细」
                found_agent = await repo.find_active_by_key(
                    tenant, user, "preference.report_style", product=AGENT)
                found_cs = await repo.find_active_by_key(
                    tenant, user, "preference.report_style", product=CS)
                await db.commit()
            assert found_agent is not None
            assert found_agent.structured_value == "concise"
            assert found_agent.product == AGENT
            assert found_cs is not None
            assert found_cs.structured_value == "detailed"
            assert found_cs.product == CS

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_scenario())
        finally:
            loop.close()

    def test_l2_sessions_isolated_by_product(self, _isolated_user):
        """同 raw user 同 session_id 在两产品下各建各的会话行与消息（L2）。"""
        user, tenant = _isolated_user
        session = f"{user}-sess"


        async def _scenario():
            from backend.memory.database import AsyncSessionLocal
            from backend.memory.repository.session_repo import SessionRepository

            async with AsyncSessionLocal() as db:
                repo = SessionRepository(db)
                # 同 (tenant, user, session_id)，两产品各建一行
                row_a = await repo.get_or_create(session, user, tenant, product=AGENT)
                row_b = await repo.get_or_create(session, user, tenant, product=CS)
                assert row_a.id != row_b.id
                assert row_a.product == AGENT and row_b.product == CS
                # 同产品重复 get_or_create 返回同一行
                row_a2 = await repo.get_or_create(session, user, tenant, product=AGENT)
                assert row_a2.id == row_a.id
                # 消息按产品隔离
                await repo.save_turn(session, "agent侧问题", "agent侧回答",
                                     product=AGENT)
                await repo.save_turn(session, "客服侧问题", "客服侧回答",
                                     product=CS)
                msgs_a = await repo.load_messages(session, product=AGENT)
                msgs_b = await repo.load_messages(session, product=CS)
                texts_a = "".join(m.content for m in msgs_a)
                texts_b = "".join(m.content for m in msgs_b)
                assert "客服侧" not in texts_a and "agent侧" not in texts_b
                # 属主校验按产品三元组
                owner_a = await repo.get_owner(session, product=AGENT)
                owner_cs = await repo.get_owner(session, product=CS)
                assert owner_a == (tenant, AGENT, user)
                assert owner_cs == (tenant, CS, user)
                await db.commit()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_scenario())
        finally:
            loop.close()

    def test_resolve_memory_identity_compat(self):
        """身份归一兼容双形态：前缀编码反解优先，raw+product 显式。"""
        from backend.memory.service import _resolve_memory_identity

        # 旧调用方：memory_user_id 前缀编码 → 前缀权威
        assert _resolve_memory_identity(
            "customer_service::u1", "t1", "") == ("u1", "t1", "customer_service")
        # 新调用方：raw + 显式 product
        assert _resolve_memory_identity(
            "u1", "t1", "customer_service") == ("u1", "t1", "customer_service")
        # 未声明 product → agent（存量语义）
        assert _resolve_memory_identity(
            "u1", "t1", "") == ("u1", "t1", "agent")
        # 未知 product → agent（不 fail-open）
        assert _resolve_memory_identity(
            "u1", "t1", "bogus") == ("u1", "t1", "agent")
        # 租户归一
        assert _resolve_memory_identity(
            "u1", "", "") == ("u1", "default", "agent")

    def test_scoped_session_id_mixes_product(self):
        """属主派生隔离键掺入 product：同 session_id 不同产品派生不同键。"""
        from backend.memory.service import MemoryService

        a = MemoryService._scoped_session_id("s1", "u1", "t1", product=AGENT)
        b = MemoryService._scoped_session_id("s1", "u1", "t1", product=CS)
        assert a != b
