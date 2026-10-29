"""_migration_residue_guard 的回归测试：stale 残留被清、fresh 残留保留。

只操作 migration-test 前缀的假行，不触碰 llm_model_role_bindings
（绑定孤儿检测分支依赖真实运行栈状态，人工验证过，不在测试中改写）。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import task_service
from backend.services.task_state import TaskManager
from backend.shared.crypto import encrypt_secret, fingerprint, last4
from backend.tests.rag._migration_residue_guard import cleanup_residue

# 时间戳必须带 UTC 时区：DB 会话时区是 Etc/UTC，naive datetime 会被按 UTC
# 解释成未来时间（宿主机为 UTC+8），stale 谓词全部失真（实测教训）。
_STALE_DT = datetime.now(timezone.utc) - timedelta(hours=2)


def _make_residue(cur, *, stale: bool, with_model: bool) -> str:
    """插入一套残留行，返回 provider_id。

    llm_models 主键是 name（'new' 全库唯一），同一测试内只有一套套件能带
    模型/凭据行——stale 套件带全套，fresh 套件仅 provider/价格/任务行
    （三类行的 stale 谓词模式相同，provider 行为足以代表 fresh 保留性）。
    """
    provider_id = "migration-test-" + uuid.uuid4().hex[:8]
    version = "migration-test-" + uuid.uuid4().hex
    age = _STALE_DT if stale else datetime.now(timezone.utc)
    cur.execute(
        "INSERT INTO llm_providers(id,display_name,driver,base_url,network_scope,billing,created_at) "
        "VALUES (%s,%s,'openai','http://127.0.0.1:1','private','local',%s)",
        (provider_id, provider_id, age),
    )
    if with_model:
        cur.execute(
            "INSERT INTO llm_models(name,provider_id,display_name,model_kind,created_at) "
            "VALUES ('new',%s,'隔离嵌入模型','embedding',%s)", (provider_id, age))
        cur.execute(
            "INSERT INTO llm_provider_credentials(provider_id,key_cipher,key_fingerprint,key_last4,updated_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (provider_id, encrypt_secret("k"), fingerprint("k"), last4("k"), age),
        )
    cur.execute(
        "INSERT INTO model_price(model_name,component,dimension,price_per_unit,"
        "unit,currency,price_table_version,source,effective_from,approval_status,reviewer_1,reviewer_2) "
        "VALUES ('new','embedding','input',1,'per_1m_tokens','CNY',%s,"
        "'provider-page',%s,'approved','test-reviewer','system')",
        (version, _STALE_DT if stale else datetime.now(timezone.utc)))
    record = TaskManager.create(
        "migration-test-admin", "守卫测试任务", tenant_id="migration-test",
        graph_name="rag_index", biz_type="embedding_rebuild", biz_id="global",
    )
    if stale:
        cur.execute("UPDATE tasks SET created_at = now() - interval '2 hours' WHERE id=%s",
                    (record.id,))
    return provider_id


def test_cleanup_removes_stale_residue_only():
    created_providers: list[str] = []
    try:
        with task_service._conn() as conn, conn.cursor() as cur:
            created_providers.append(_make_residue(cur, stale=True, with_model=True))
            created_providers.append(_make_residue(cur, stale=False, with_model=False))
        deleted = cleanup_residue()
        # stale 套件被清（本测试外的同名前缀历史残留也一并清，用 >= 断言）
        assert deleted["providers"] >= 1 and deleted["models"] >= 1
        assert deleted["credentials"] >= 1 and deleted["model_price"] >= 1
        assert deleted["tasks"] >= 1
        with task_service._conn() as conn, conn.cursor() as cur:
            stale_provider, fresh_provider = created_providers
            for table, col, pid in (
                ("llm_providers", "id", stale_provider),
                ("llm_models", "provider_id", stale_provider),
                ("llm_provider_credentials", "provider_id", stale_provider),
            ):
                cur.execute(f"SELECT count(*) FROM {table} WHERE {col}=%s", (pid,))
                assert cur.fetchone()[0] == 0, f"{table} 的 stale 残留未被清扫"
            # fresh 残留（可能属于并行活跃会话）必须保留
            cur.execute("SELECT count(*) FROM llm_providers WHERE id=%s", (fresh_provider,))
            assert cur.fetchone()[0] == 1, "fresh 残留被误清——会弄坏并行运行的测试"
            # model_price 是 append-only：stale 被软关、fresh 仍开放
            cur.execute(
                "SELECT count(*) FROM model_price WHERE price_table_version LIKE 'migration-test-%' "
                "AND effective_from < now() - interval '2 hours' AND effective_to IS NULL")
            assert cur.fetchone()[0] == 0, "stale 价格行未被软关"
    finally:
        # 对称清理：即使中途断言失败，本测试造的每一行都要收走
        with task_service._conn() as conn, conn.cursor() as cur:
            for pid in created_providers:
                cur.execute("DELETE FROM llm_models WHERE provider_id=%s", (pid,))
                cur.execute("DELETE FROM llm_provider_credentials WHERE provider_id=%s", (pid,))
                cur.execute("DELETE FROM llm_providers WHERE id=%s", (pid,))
            cur.execute(
                "UPDATE model_price SET effective_to=now() "
                "WHERE price_table_version LIKE 'migration-test-%' AND effective_to IS NULL "
                "AND effective_from <= now()")
            cur.execute(
                "SELECT count(*) FROM model_price WHERE price_table_version LIKE 'migration-test-%' "
                "AND effective_to IS NULL AND effective_from > now()")
            leftover = cur.fetchone()[0]
            if leftover:
                # 历史失败轮累积的坏行（naive 时间写成未来值）：append-only 触发器
                # 不允许软关或删除，治理上只能保留（model='new' 无生产计费影响）。
                print(f"[residue-guard] {leftover} 行历史坏价格行无法软关（append-only 限制），保留")


def test_cleanup_is_safe_to_run_repeatedly():
    first = cleanup_residue()
    second = cleanup_residue()
    assert isinstance(first, dict) and isinstance(second, dict)
    # 连续两次调用不抛错即幂等（行数可以不同：并行会话在窗口外落了新残留）
    assert set(second) == set(first)
