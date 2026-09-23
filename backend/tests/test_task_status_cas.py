"""update_status 并发 CAS 重判语义（2026-09-23 P1-5 回归）

场景：worker 的 update_status 在初始读时行还是 RUNNING，随后 sweeper 把
行认领回 PENDING → 第一次 CAS（WHERE RUNNING）落空 → 重判 PENDING→FAILED
合法 → 第二次 CAS 必须以 PENDING 为期望值。修复前复用旧快照 RUNNING →
必然 0 行且无人检查 rowcount → 终态写静默丢失，任务卡 PENDING 被重投
重跑、失败原因丢失。

竞态注入手法：包装 _conn 的 cursor，按「抢占计划表」在每次 UPDATE 执行
前用独立真实连接改写行状态（模拟另一 worker 的原生 SQL 通道）：
1. 计划 [PENDING]：第一次 CAS 落空、重判合法 → 以 latest_status 落终态；
2. 终态被抢（SUCCESS 封闭）→ 重判拒绝、不越权改写；
3. 计划 [PENDING, RUNNING]：第二次 CAS 仍被抢占 → 上抛，绝不静默成功。
"""
import uuid

import pytest

from backend.models.task import IllegalTaskTransition, TaskStatus


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
        task_service._conn().close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 CAS 竞态测试: {e}")
    from backend.services import task_service

    return task_service


def _create_running(pg):
    task = pg.create_task(f"cas-{uuid.uuid4().hex[:8]}", "CAS 竞态测试")
    pg.update_status(task.id, TaskStatus.RUNNING)
    return task


def _cleanup(pg, task_id):
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


def _racing_update_status(ts, task_id, real_conn, preempt_plan, **kw):
    """在 update_status 的第 n 次 UPDATE 前按计划表抢占行状态。

    preempt_plan: [第一次 UPDATE 前置入的状态, 第二次 UPDATE 前置入的, ...]
    """
    plan = list(preempt_plan)

    class _PlannedCursor:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, params=None):
            flat = " ".join(sql.split())
            # 只命中被测的终态写（WHERE 以 AND status = %s 收尾），
            # 不误伤抢占写入自身（其 WHERE 只有 id）
            if plan and flat.startswith("UPDATE tasks SET") \
                    and flat.endswith("AND status = %s"):
                with real_conn() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE tasks SET status = %s WHERE id = %s",
                                (plan.pop(0), task_id))
            self._cur.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._cur, name)

        def __enter__(self):
            self._cur.__enter__()
            return self

        def __exit__(self, *exc):
            return self._cur.__exit__(*exc)

    class _PlannedConn:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return _PlannedCursor(self._conn.cursor())

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

    real = ts._conn

    def _factory():
        return _PlannedConn(real())

    with pytest.MonkeyPatch.context() as m:
        m.setattr(ts, "_conn", _factory)
        return ts.update_status(task_id, **kw)


def test_rejudge_second_cas_binds_latest_status(pg):
    """初始读 RUNNING → 被认领回 PENDING → 终审 FAILED 必须以 PENDING 落库。

    修复前：第二次 UPDATE 仍绑旧快照 RUNNING → 0 行且无人检查 → 任务卡
    PENDING（本用例在修复代码上必红）。
    """
    from backend.services import task_service as ts

    task = _create_running(pg)
    real_conn = ts._conn
    try:
        _racing_update_status(
            ts, task.id, real_conn,
            preempt_plan=[TaskStatus.PENDING.value],
            status=TaskStatus.FAILED, error_message="旧 worker 终审")
        record = pg.get_task(task.id)
        assert record.status == TaskStatus.FAILED
        assert record.error_message == "旧 worker 终审"
    finally:
        _cleanup(pg, task.id)


def test_rejudge_illegal_transition_rejected(pg):
    """重判非法（SUCCESS 终态封闭）：拒绝写入且状态不被越权改写。"""
    task = _create_running(pg)
    try:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE tasks SET status = %s WHERE id = %s",
                        (TaskStatus.SUCCESS.value, task.id))
        with pytest.raises(IllegalTaskTransition):
            pg.update_status(task.id, TaskStatus.FAILED,
                             error_message="不应落库")
        assert pg.get_task(task.id).status == TaskStatus.SUCCESS
    finally:
        _cleanup(pg, task.id)


def test_second_cas_preemption_not_silent(pg):
    """第二次 CAS 仍被抢占 → 上抛 IllegalTaskTransition，绝不静默成功。"""
    from backend.services import task_service as ts

    task = _create_running(pg)
    real_conn = ts._conn
    try:
        with pytest.raises(IllegalTaskTransition):
            _racing_update_status(
                ts, task.id, real_conn,
                preempt_plan=[TaskStatus.PENDING.value,   # 一抢：触发重判
                              TaskStatus.RUNNING.value],  # 二抢：二次 CAS 落空
                status=TaskStatus.FAILED, error_message="二次抢占终审")
        # 被抢占后终态写不得发生，行保持抢占方写入的 RUNNING
        record = pg.get_task(task.id)
        assert record.status == TaskStatus.RUNNING
        assert record.error_message != "二次抢占终审"
    finally:
        _cleanup(pg, task.id)
