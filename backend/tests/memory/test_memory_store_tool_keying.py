"""memory_store_tool 客服产品形态命名空间回归（评审 P1 修复锁定，2026-11-10）。

修复前：工具通道 user_id 是 memory_user_id 兼容层前缀编码
（客服产品 = "customer_service::u"），memory_store_tool 又显式传 product 并
直调 store_with_resolution（不经 MemoryService 归一，conflict 层对 user_id
原样落库）——客服显式记忆落 (customer_service, "customer_service::u") 分裂
命名空间，检索/画像/删除（走 MemoryService 归一后是 (customer_service,
裸 user_id)）永不可见。
修复后：工具写入前经 _resolve_memory_identity 归一（前缀权威优先），
写入 (customer_service, 裸 user_id)，与检索侧同命名空间可达。
"""
from __future__ import annotations

import uuid

import pytest

from backend.tests.memory.conftest import require_memory_pg


def _require_product_column() -> None:
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
    # 纯字母裸值（规避 PII 脱敏误伤与 jieba 切分歧义）；LIKE 前缀清理
    user = f"utoolkeying{uuid.uuid4().hex[:8]}"
    tenant = "tenant-tool-keying"
    yield user, tenant
    # 同步清理：memory_records / chat_sessions（chat_messages 随会话级联）
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM memory_records WHERE user_id LIKE %s",
                           (f"{user}%",))
            cursor.execute("DELETE FROM chat_sessions WHERE session_id LIKE %s",
                           (f"{user}%",))
        conn.commit()


class _FakeEmbedding:
    """容器测试无供应商凭据：文本确定性伪随机向量（同隔离测试模式）。"""

    def embed_query(self, text: str) -> list[float]:
        import hashlib

        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        return [(seed >> i) & 1 for i in range(1024)]


class TestStoreToolKeying:
    def test_cs_product_write_reachable_by_search(self, _isolated_user,
                                                  monkeypatch):
        """客服产品下工具写入 → 归一三元组落库，且检索侧可达。"""
        user, tenant = _isolated_user
        session = f"{user}-sess"
        content = f"{user} 偏好企业微信沟通"

        # 复刻工具链路的 ContextVar 形态（客服产品入口，可信身份注入）
        from backend.core.product_context import memory_user_id, product_by_id
        from backend.core.request_context import (
            get_current_product_id, set_product_context, set_session_id,
            set_tool_tenant_id, set_tool_user_id,
        )

        set_product_context("customer_service", "test")
        set_session_id(session)
        set_tool_user_id(user)
        set_tool_tenant_id(tenant)
        try:
            # 工具实际拿到的是兼容层编码形态
            keyed_user = memory_user_id(product_by_id(
                get_current_product_id()), user)
            assert keyed_user == f"customer_service::{user}"

            # 证据门数据源 stub：返回含用户原话的会话消息。替换体必须是
            # coroutine 工厂（run_tool 要求 coro_factory() 产出协程对象）；
            # 类级替换使 memory 后台 loop 中的协程同样生效。
            from backend.memory.service import MemoryService

            async def _fake_get_session_messages(
                self, sid, user_id=None, tenant_id=None, product="",
            ):
                return {"session_id": sid,
                        "messages": [{"id": 1, "role": "user",
                                      "content": f"请记住{content}",
                                      "created_at": "2026-11-10T00:00:00"}]}

            monkeypatch.setattr(
                MemoryService, "get_session_messages",
                lambda self, sid, user_id=None, tenant_id=None, product="":
                _fake_get_session_messages(
                    self, sid, user_id=user_id, tenant_id=tenant_id,
                    product=product),
            )
            # embedding stub（类级 property 覆盖：工具内部新构造的实例同样生效）
            from backend.memory.long_term import LongTermMemory

            monkeypatch.setattr(
                LongTermMemory, "embedding",
                property(lambda self: _FakeEmbedding()))

            # 走真实工具（证据门 → PII → store_with_resolution 全链）
            from backend.tools.memory import memory_store_tool

            output = memory_store_tool.invoke({"content": content})
            assert output.startswith("✅"), output

            # repo 物理层断言：落库三元组 = (tenant, customer_service, 裸 user)
            # 修复前此断言失败：user_id 落成 "customer_service::<user>" 前缀编码
            import psycopg2

            from backend.config.database import MEMORY_DB_CONFIG

            with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT tenant_id, product, user_id, origin "
                        "FROM memory_records WHERE user_id LIKE %s",
                        (f"{user}%",))
                    rows = cursor.fetchall()
            assert rows, "memory_store_tool 未落库"
            assert all(r[0] == tenant for r in rows), rows
            assert all(r[1] == "customer_service" for r in rows), rows
            assert all(r[2] == user for r in rows), rows
            assert all(r[3] == "explicit" for r in rows), rows

            # 检索侧可达：MemoryService.search 内部经同一归一函数，
            # 传入兼容层编码形态（与工具原始输入同形态）
            from backend.memory.manager import memory_manager

            facts = memory_manager.run_tool(
                lambda: memory_manager.service.search(
                    content, session, user_id=keyed_user, top_k=5,
                    tenant_id=tenant))
            assert facts, "写入的记忆检索不可达（命名空间分裂）"
            assert any(content in f.content for f in facts), facts
        finally:
            # ContextVar 复位到 request_context 各字段默认值，避免泄漏到
            # 同进程后续测试
            from backend.core.request_context import (
                set_product_context, set_session_id, set_tool_tenant_id,
                set_tool_user_id,
            )

            set_product_context("internal", "internal_call")
            set_session_id("multi-agent-default")
            set_tool_user_id("")
            set_tool_tenant_id("")
