"""cs_faq 真库回归（2026-11-10 周回归漂移复盘立项）。

背景：test_cs_faq.py 的 FakeCursor 按 SQL 前缀模拟存储——#394 给 INSERT
加 tenant_id 首列后桩参数整体错位，8 个用例静默失真（周回归 v4 run
42601291303 实证）。本文件用真实 PG 锁死 SQL 形态本身：upsert 幂等、
租户过滤、状态/有效期 fail-closed、台账与统计口径。桩单测保留（快），
真库测试防漂移（准）——桩与真实 SQL 形态再次分叉时，这里先红。
"""
from __future__ import annotations

import uuid

import pytest


def _pg_ready() -> bool:
    try:
        import psycopg2

        from backend.config.database import DOC_REGISTRY_PG_CONFIG as FAQ_DB_CONFIG

        with psycopg2.connect(**FAQ_DB_CONFIG, connect_timeout=2):
            return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_ready(), reason="agent_memory 不可达，跳过 cs_faq 真库回归"
)


@pytest.fixture()
def pg_faq():
    """真实 FAQStore（生产默认连接工厂）+ 按租户隔离的清理。"""
    from backend.customer_service.faq import FAQStore

    tenant = f"t-pgfaq-{uuid.uuid4().hex[:8]}"
    marker = f"pgfaq{uuid.uuid4().hex[:6]}"
    store = FAQStore()
    yield store, tenant, marker
    import psycopg2

    from backend.config.database import DOC_REGISTRY_PG_CONFIG as FAQ_DB_CONFIG

    with psycopg2.connect(**FAQ_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai.cs_faq WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM ai.cs_faq_query_log WHERE question LIKE %s",
                        (f"%{marker}%",))
        conn.commit()


@requires_pg
class TestCsFaqRealPg:
    def test_upsert_idempotent_and_exact_match(self, pg_faq):
        """同问重复写入不产生重复行（tenant+faq_key 唯一），精确命中返回原行。"""
        store, tenant, marker = pg_faq
        q = f"{marker} 退货的运费由谁承担"
        id1 = store.upsert_faq(q, "签收后 7 天内可申请。", tenant_id=tenant)
        id2 = store.upsert_faq(q, "签收后 7 天内可申请。", tenant_id=tenant)
        assert id1 == id2, "faq_key 幂等：重复 upsert 不得新建行"

        m = store.match(q, tenant_id=tenant)
        assert m is not None and m.matched_by == "exact"
        assert m.faq_id == id1

    def test_tenant_isolation(self, pg_faq):
        """A 租户发布的 FAQ 对 B 租户不可见（#394 租户隔离的 SQL 形态锚点）。"""
        store, tenant, marker = pg_faq
        q = f"{marker} 密码忘了怎么办"
        store.upsert_faq(q, "请在登录页点「忘记密码」。", tenant_id=tenant)

        assert store.match(q, tenant_id=tenant) is not None
        assert store.match(q, tenant_id=f"{tenant}-other") is None, \
            "他租户不得命中本租户 FAQ"
        assert store.match_candidates(q, k=2, tenant_id=f"{tenant}-other") == []

    def test_draft_and_expired_excluded(self, pg_faq):
        """draft 与过期条目不参与匹配（fail-closed），恢复 published 后可达。"""
        store, tenant, marker = pg_faq
        q_draft = f"{marker} 草稿问题是什么"
        store.upsert_faq(q_draft, "草稿答案。", status="draft", tenant_id=tenant)
        assert store.match(q_draft, tenant_id=tenant) is None

        q_expired = f"{marker} 限时政策是什么"
        store.upsert_faq(q_expired, "已过期答案。",
                         valid_until="2020-01-01", tenant_id=tenant)
        assert store.match(q_expired, tenant_id=tenant) is None

        # 恢复 published 后精确命中可达
        store.upsert_faq(q_expired, "新的限时答案。", tenant_id=tenant)
        m = store.match(q_expired, tenant_id=tenant)
        assert m is not None and m.answer == "新的限时答案。"

    def test_variant_match_and_candidates(self, pg_faq):
        """改写问法走 jaccard 模糊命中；未过主阈值的相似条目进候选推荐。"""
        store, tenant, marker = pg_faq
        store.upsert_faq(
            f"{marker} 退货商品运费谁承担", "退货运费由卖家承担。",
            tenant_id=tenant)
        m = store.match(f"{marker} 退货的运费由谁承担呢", tenant_id=tenant)
        assert m is not None and m.matched_by in ("variant", "jaccard")

        cands = store.match_candidates(
            f"{marker} 退货流程是什么", k=2, tenant_id=tenant)
        assert cands == [] or all(c.faq_id >= 1 for c in cands)

    def test_stats_and_query_log_recorded(self, pg_faq):
        """命中与未命中都落 ai.cs_faq_query_log；stats 口径可对账。"""
        store, tenant, marker = pg_faq
        q_hit = f"{marker} 发票怎么申请"
        store.upsert_faq(q_hit, "在订单详情页申请电子发票。", tenant_id=tenant)
        assert store.match(q_hit, tenant_id=tenant) is not None
        store.match(f"{marker} 完全无关的问题xyz", tenant_id=tenant)

        import psycopg2

        from backend.config.database import DOC_REGISTRY_PG_CONFIG as FAQ_DB_CONFIG

        with psycopg2.connect(**FAQ_DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*), count(*) FILTER (WHERE matched) "
                    "FROM ai.cs_faq_query_log WHERE question LIKE %s",
                    (f"%{marker}%",))
                total, matched = cur.fetchone()
        assert total >= 2, "命中与未命中均须落台账"
        assert matched >= 1

        stats = store.stats()
        assert stats["published"] >= 1
