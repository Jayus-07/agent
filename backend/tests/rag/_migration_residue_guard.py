"""embedding 迁移验收测试的残留守卫（跨进程兜底）。

test_embedding_migration_worker 会临时把 embedding 角色绑定劫持到
'migration-test-*' 供应商/模型，在 fixture finally 中恢复并清理；
进程被 pytest-timeout 或 kill 硬杀时 finally 不会执行，残留会让运行栈
的 embedding 热切换到死端口（2026-10-29 实证：本机共享栈 embedding
全挂、知识问答检索全拒答）。

自愈触发点：
- worker_env fixture setup：先清上次残留，防在坏状态上叠测；
- conftest.pytest_sessionfinish：任意测试会话收尾时清扫历史残留。

并行安全：只清 created_at/updated_at 早于 STALE_MINUTES 的残留——
正在运行的测试劫持窗口是分钟级，fresh 残留属于活跃会话，不可动。
绑定值本身不自动改写（无法确知应恢复成什么），孤儿绑定仅显式告警，
修复指引见 docs/tasks/invoice-faq-recall/PROGRESS.md 2026-10-30 记录。
"""
import logging

logger = logging.getLogger(__name__)

STALE_MINUTES = 60
_PROVIDER_LIKE = "migration-test-%"


def _stale(col: str) -> str:
    return f"{col} < now() - interval '{STALE_MINUTES} minutes'"


def cleanup_residue() -> dict[str, int]:
    """清扫历史崩溃残留，返回各表处理行数。连接失败向上抛，由调用方定策略。

    model_price 是 append-only 治理表（触发器 reject_model_price_mutation
    只允许关闭 effective_to、禁止删行），残留价格行按治理规则软关而非删除。
    """
    from backend.services import task_service

    # tasks 表由 ensure_schema 幂等建——CI 隔离库无人触发建表时裸 DELETE
    # 会 UndefinedTable（v20 run 41340426306 rag 6 例 ERROR 实证；
    # 本地主栈库有运行栈建的表，不暴露）
    task_service.ensure_schema()
    deleted: dict[str, int] = {}
    with task_service._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM llm_provider_credentials WHERE provider_id LIKE %s "
            f"AND {_stale('updated_at')}", (_PROVIDER_LIKE,))
        deleted["credentials"] = cur.rowcount
        cur.execute(
            "DELETE FROM llm_models WHERE provider_id LIKE %s "
            f"AND {_stale('created_at')}", (_PROVIDER_LIKE,))
        deleted["models"] = cur.rowcount
        cur.execute(
            "DELETE FROM llm_providers WHERE id LIKE %s "
            f"AND {_stale('created_at')}", (_PROVIDER_LIKE,))
        deleted["providers"] = cur.rowcount
        # effective_from <= now() 防御：历史坏行（naive 时间被写成未来值，
        # 触发器白名单要求 effective_to >= effective_from）无法软关，跳过
        # 以免整体失败；这类行仅涉 model_name='new'（测试模型），无生产计费影响。
        cur.execute(
            "UPDATE model_price SET effective_to = now() "
            "WHERE price_table_version LIKE %s AND effective_to IS NULL "
            f"AND {_stale('effective_from')} AND effective_from <= now()", (_PROVIDER_LIKE,))
        deleted["model_price"] = cur.rowcount
        cur.execute(
            "DELETE FROM tasks WHERE tenant_id = 'migration-test' "
            f"AND {_stale('created_at')}")
        deleted["tasks"] = cur.rowcount
        # 未终态的测试重建任务会占住全局重建锁（biz_id='global'，后续
        # submit_rebuild 全部 ModelConfigConflict），按测试自身的恢复模式
        # 收口为 FAILED（tenant_id='migration-test' 是测试专属标记，
        # 真实重建任务不会落在该租户）。
        cur.execute(
            "UPDATE tasks SET status='FAILED', execution_id='', lease_expires_at=NULL "
            "WHERE tenant_id = 'migration-test' AND status NOT IN "
            "('SUCCESS', 'FAILED', 'CANCELLED') "
            f"AND {_stale('created_at')}")
        deleted["stuck_tasks"] = cur.rowcount
    return deleted


def orphan_binding_warning() -> str | None:
    """embedding 绑定若缺失、或指向不存在/测试残留的模型，返回告警文本；否则 None。"""
    from backend.services.task_service import _conn

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT model_name FROM llm_model_role_bindings WHERE role='embedding'")
        row = cur.fetchone()
        if row is None:
            return "llm_model_role_bindings 缺少 embedding 角色行（检索链将无法向量化）"
        (model_name,) = row
        cur.execute(
            "SELECT count(*) FROM llm_models m JOIN llm_providers p ON p.id = m.provider_id "
            "WHERE m.name = %s AND p.id NOT LIKE %s", (model_name, _PROVIDER_LIKE))
        (alive,) = cur.fetchone()
        if not alive:
            return (f"embedding 绑定指向不存在或测试残留的模型 {model_name!r}"
                    "——疑似崩溃残留，请人工恢复绑定（参照 PROGRESS 2026-10-30 修复记录）")
    return None
