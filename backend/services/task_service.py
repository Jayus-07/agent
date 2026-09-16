"""services/task_service.py — 任务持久层（agent_memory 库）。

职责：tasks / agent_checkpoints 两张表的 CRUD。
- 连接：psycopg3 直连 MEMORY_DB_CONFIG（与主图 checkpointer 同库同源）
- 建表：ensure_schema() 幂等 DDL（首次调用自动执行，见 tasks/schema.sql）
- 隔离：所有用户维度查询强制 WHERE user_id = %s（禁止跨用户读）

注意：同步阻塞 IO。API 层经 FastAPI 线程池调用无碍；
Worker（Celery prefork）天然同步，直接调用。
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from backend.config.database import MEMORY_DB_CONFIG
from backend.models.task import TaskRecord, TaskStatus
from backend.shared.logger import logger

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "tasks" / "schema.sql"

_schema_lock = threading.Lock()
_schema_ready = False


def _dsn() -> str:
    c = MEMORY_DB_CONFIG
    return (f"postgresql://{c['user']}:{c['password']}"
            f"@{c['host']}:{c['port']}/{c['dbname']}")


def _conn():
    import psycopg

    return psycopg.connect(_dsn(), autocommit=True)


def ensure_schema() -> None:
    """幂等建表（进程内只执行一次；多进程并发安全由 IF NOT EXISTS 保证）。"""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(sql)
            _schema_ready = True
            logger.info("[TaskService] schema ensured (tasks + agent_checkpoints)")
        except Exception as e:
            # 建表失败不静默：任务系统不可用必须可见
            logger.error("[TaskService] ensure_schema failed: %s", e)
            raise


# ═══════════════════════════════════════════════════
# 写路径
# ═══════════════════════════════════════════════════

def create_task(user_id: str, query: str, *, tenant_id: str = "default",
                graph_name: str = "main",
                trace_id: str = "",
                biz_type: str = "", biz_id: str = "",
                parent_task_id: str = "") -> TaskRecord:
    """创建 PENDING 任务并落库。thread_id 全局唯一（checkpoint 定位键）。"""
    from backend.config.tasks import CELERY_MAX_RETRIES

    ensure_schema()
    task_id = str(uuid.uuid4())
    thread_id = f"task-{task_id}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (id, user_id, tenant_id, graph_name, status,
                               input, thread_id, trace_id,
                               biz_type, biz_id, parent_task_id, max_retries)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, user_id, tenant_id, graph_name,
             TaskStatus.PENDING.value,
             json.dumps({"query": query}, ensure_ascii=False),
             thread_id, trace_id, biz_type, biz_id,
             parent_task_id or None, CELERY_MAX_RETRIES),
        )
    return get_task(task_id)  # type: ignore[return-value]


def mark_queued(task_id: str, celery_task_id: str, *,
                queue: str = "agent") -> None:
    """apply_async 成功后回填：celery id + 队列 + queued_at（排队耗时起点）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET celery_task_id = %s, queue = %s, "
            "queued_at = now(), updated_at = now() WHERE id = %s",
            (celery_task_id, queue, task_id),
        )


def update_status(task_id: str, status: TaskStatus, *,
                  error_message: str = "",
                  error_type: str | None = None,
                  traceback_text: str | None = None,
                  worker: str | None = None,
                  duration_ms: int | None = None,
                  progress: str | None = None,
                  checkpoint_id: str | None = None,
                  output: dict | None = None,
                  celery_task_id: str | None = None) -> None:
    """状态迁移 + 可选字段一并更新（单条 UPDATE，避免多写竞态）。

    时间戳自动治理：
    - RUNNING → started_at（COALESCE 保留首次启动，重试不覆盖）
    - 终态 → finished_at，且 started_at 非空时自动计算 duration_ms
    """
    ensure_schema()
    sets = ["status = %s", "error_message = %s", "updated_at = now()"]
    args: list = [status.value, error_message]
    if error_type is not None:
        sets.append("error_type = %s")
        args.append(error_type[:128])
    if traceback_text is not None:
        sets.append("traceback = %s")
        args.append(traceback_text[:20000])
    if worker is not None:
        sets.append("worker = %s")
        args.append(worker[:128])
    if duration_ms is not None:
        sets.append("duration_ms = %s")
        args.append(int(duration_ms))
    if progress is not None:
        sets.append("progress = %s")
        args.append(progress[:500])
    if checkpoint_id is not None:
        sets.append("checkpoint_id = %s")
        args.append(checkpoint_id)
    if output is not None:
        sets.append("output = %s")
        args.append(json.dumps(output, ensure_ascii=False, default=str))
    if celery_task_id is not None:
        sets.append("celery_task_id = %s")
        args.append(celery_task_id)
    if status == TaskStatus.RUNNING:
        sets.append("started_at = COALESCE(started_at, now())")
    if status.is_terminal():
        sets.append("finished_at = now()")
        # 未显式给耗时时自动补算（执行段耗时，不含排队）
        if duration_ms is None:
            sets.append("duration_ms = CASE WHEN started_at IS NOT NULL THEN "
                        "(EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int "
                        "ELSE duration_ms END")
    args.append(task_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s", args)


def update_progress(task_id: str, node_name: str, progress: str = "") -> None:
    """节点级进度更新（Worker 每个节点边界调用）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET current_node = %s, progress = %s, "
            "checkpoint_id = thread_id, updated_at = now() WHERE id = %s",
            (node_name, progress[:500], task_id),
        )


def increment_retry(task_id: str) -> int:
    """重试计数 +1，返回新值（Celery self.request.retries 的 DB 侧镜像）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET retry_count = retry_count + 1, updated_at = now() "
            "WHERE id = %s RETURNING retry_count", (task_id,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def append_checkpoint(task_id: str, node_name: str, state: dict) -> None:
    """节点执行完成 → 追加 agent_checkpoints 历史（节点输出快照）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_checkpoints (task_id, node_name, state_json) "
            "VALUES (%s, %s, %s)",
            (task_id, node_name,
             json.dumps(state, ensure_ascii=False, default=str)),
        )


# ═══════════════════════════════════════════════════
# 读路径（用户隔离强制）
# ═══════════════════════════════════════════════════

def get_task(task_id: str) -> TaskRecord | None:
    """按 id 读取（内部/Worker 用；API 层必须走 get_task_for_user）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
    return TaskRecord.from_row(dict(zip(cols, row)))


def get_task_for_user(task_id: str, user_id: str,
                      tenant_id: str = "") -> TaskRecord | None:
    """用户维度读取：user_id 不匹配返回 None（API 层转 403/404）。

    tenant_id 传入时追加租户过滤（多租户部署开启）。
    """
    ensure_schema()
    sql = "SELECT * FROM tasks WHERE id = %s AND user_id = %s"
    args: list = [task_id, user_id]
    if tenant_id:
        sql += " AND tenant_id = %s"
        args.append(tenant_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
    return TaskRecord.from_row(dict(zip(cols, row)))


def list_tasks_for_user(user_id: str, *, tenant_id: str = "",
                        status: str = "", limit: int = 20) -> list[TaskRecord]:
    """用户任务历史（created_at 倒序）。"""
    ensure_schema()
    sql = "SELECT * FROM tasks WHERE user_id = %s"
    args: list = [user_id]
    if tenant_id:
        sql += " AND tenant_id = %s"
        args.append(tenant_id)
    if status:
        sql += " AND status = %s"
        args.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    args.append(max(1, min(int(limit), 100)))
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return [TaskRecord.from_row(dict(zip(cols, r))) for r in rows]


def get_user_input(task_id: str) -> str:
    """读取最近一次 resume API 注入的用户输入（__user_input__ checkpoint 行）。

    读取即消费（删除该行），避免下次重试重复注入。
    """
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT state_json->>'user_input' FROM agent_checkpoints "
            "WHERE task_id = %s AND node_name = '__user_input__' "
            "ORDER BY id DESC LIMIT 1", (task_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return ""
        cur.execute(
            "DELETE FROM agent_checkpoints "
            "WHERE task_id = %s AND node_name = '__user_input__'", (task_id,))
        return str(row[0])


def list_checkpoints(task_id: str, user_id: str) -> list[dict]:
    """任务节点 checkpoint 历史（先校验归属再返回）。"""
    owner = get_task_for_user(task_id, user_id)
    if owner is None:
        return []
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, node_name, state_json, created_at "
            "FROM agent_checkpoints WHERE task_id = %s ORDER BY id", (task_id,))
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


# ═══════════════════════════════════════════════════
# 管理端查询（跨用户，调用方必须已过管理员闸）
# ═══════════════════════════════════════════════════

def list_tasks_admin(
    *,
    status: str = "",
    graph_name: str = "",
    queue: str = "",
    worker: str = "",
    user_id: str = "",
    tenant_id: str = "",
    biz_type: str = "",
    biz_id: str = "",
    trace_id: str = "",
    retries_gt: int | None = None,
    hours: float = 24 * 7,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[TaskRecord], int]:
    """管理员全局任务列表（多条件 AND，created_at 倒序，返回 (records, total)）。"""
    ensure_schema()
    where = ["t.created_at >= now() - (%s || ' hours')::interval"]
    args: list = [float(hours)]
    if status:
        where.append("t.status = %s")
        args.append(status)
    if graph_name:
        where.append("t.graph_name = %s")
        args.append(graph_name)
    if queue:
        where.append("t.queue = %s")
        args.append(queue)
    if worker:
        where.append("t.worker ILIKE %s")
        args.append(f"%{worker}%")
    if user_id:
        where.append("t.user_id = %s")
        args.append(user_id)
    if tenant_id:
        where.append("t.tenant_id = %s")
        args.append(tenant_id)
    if biz_type:
        where.append("t.biz_type = %s")
        args.append(biz_type)
    if biz_id:
        where.append("t.biz_id = %s")
        args.append(biz_id)
    if trace_id:
        where.append("t.trace_id = %s")
        args.append(trace_id)
    if retries_gt is not None:
        where.append("t.retry_count > %s")
        args.append(int(retries_gt))
    where_sql = " AND ".join(where)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM tasks t WHERE {where_sql}", args)
        total = int(cur.fetchone()[0])  # type: ignore[index]
        cur.execute(
            f"SELECT t.* FROM tasks t WHERE {where_sql} "
            "ORDER BY t.created_at DESC LIMIT %s OFFSET %s",
            [*args, max(1, min(int(limit), 200)), max(0, int(offset))],
        )
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    records = [TaskRecord.from_row(dict(zip(cols, r))) for r in rows]
    return records, total


def stats_tasks(*, hours: float = 24) -> dict:
    """任务统计：窗口内各状态计数 / 成功率 / 失败率 / 平均与 P95 耗时 / 重试任务数。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, count(*) AS n,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,
                   avg(duration_ms) AS avg_ms
            FROM tasks
            WHERE created_at >= now() - (%s || ' hours')::interval
            GROUP BY status
            """,
            (float(hours),),
        )
        rows = cur.fetchall()
        by_status = {r[0]: {"count": int(r[1]),
                            "p95_duration_ms": int(r[2]) if r[2] is not None else None,
                            "avg_duration_ms": int(r[3]) if r[3] is not None else None}
                     for r in rows}
        cur.execute(
            "SELECT count(*) FROM tasks WHERE created_at >= "
            "now() - (%s || ' hours')::interval AND retry_count > 0",
            (float(hours),),
        )
        retried = int(cur.fetchone()[0])  # type: ignore[index]
    total = sum(v["count"] for v in by_status.values())
    success = by_status.get("SUCCESS", {}).get("count", 0)
    failed = by_status.get("FAILED", {}).get("count", 0)
    finished = success + failed
    p95 = [v["p95_duration_ms"] for v in by_status.values() if v["p95_duration_ms"] is not None]
    return {
        "window_hours": hours,
        "total": total,
        "by_status": by_status,
        "success_rate": round(success / finished, 4) if finished else None,
        "failure_rate": round(failed / finished, 4) if finished else None,
        "p95_duration_ms": max(p95) if p95 else None,
        "retried_count": retried,
    }


def list_checkpoints_admin(task_id: str) -> list[dict]:
    """管理员视角节点 checkpoint 历史（不做 user 归属校验，闸在上层）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, node_name, state_json, created_at "
            "FROM agent_checkpoints WHERE task_id = %s ORDER BY id", (task_id,))
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
