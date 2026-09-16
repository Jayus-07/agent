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
                graph_name: str = "main") -> TaskRecord:
    """创建 PENDING 任务并落库。thread_id 全局唯一（checkpoint 定位键）。"""
    ensure_schema()
    task_id = str(uuid.uuid4())
    thread_id = f"task-{task_id}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (id, user_id, tenant_id, graph_name, status,
                               input, thread_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, user_id, tenant_id, graph_name,
             TaskStatus.PENDING.value,
             json.dumps({"query": query}, ensure_ascii=False),
             thread_id),
        )
    return get_task(task_id)  # type: ignore[return-value]


def update_status(task_id: str, status: TaskStatus, *,
                  error_message: str = "",
                  progress: str | None = None,
                  checkpoint_id: str | None = None,
                  output: dict | None = None,
                  celery_task_id: str | None = None) -> None:
    """状态迁移 + 可选字段一并更新（单条 UPDATE，避免多写竞态）。"""
    ensure_schema()
    sets = ["status = %s", "error_message = %s", "updated_at = now()"]
    args: list = [status.value, error_message]
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
