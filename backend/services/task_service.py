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
                conversation_id: str = "",
                trace_id: str = "",
                biz_type: str = "", biz_id: str = "",
                parent_task_id: str = "",
                extra_input: dict | None = None) -> TaskRecord:
    """创建 PENDING 任务并落库。thread_id 全局唯一（checkpoint 定位键）。

    extra_input：执行器重投所需的业务参数（如 rag_index 的索引 kwargs），
    与 query 合并进 input JSONB——resume 时执行器路由依赖它。
    """
    from backend.config.tasks import CELERY_MAX_RETRIES

    ensure_schema()
    task_id = str(uuid.uuid4())
    thread_id = f"task-{task_id}"
    input_payload = {"query": query, **(extra_input or {})}
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (id, user_id, tenant_id, graph_name,
                               conversation_id, status,
                               input, thread_id, trace_id,
                               biz_type, biz_id, parent_task_id, max_retries)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, user_id, tenant_id, graph_name,
             conversation_id[:128], TaskStatus.PENDING.value,
             json.dumps(input_payload, ensure_ascii=False, default=str),
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


def try_acquire_lease(task_id: str, *, worker: str | None = None,
                      stale_running_seconds: int | None = None,
                      lease_ttl_seconds: int | None = None) -> str | None:
    """原子抢执行租约（Phase1 Step3 执行权锁：max_concurrent_executor_per_task=1）。

    acks_late + Redis visibility timeout 内未 ack 会重投第二个 Worker；
    若不抢租约，第二个 Worker 会与第一个并发跑同一 thread_id
    （LLM 重复烧钱、step_results 互踩）。

    返回值：认领成功 = 本次执行租约 ``execution_id``（uuid，owner 维度二：
    worker 名 + 租约实例 id，接管/重投时换发新值可审计）；认领失败 = None。

    可认领态 = PENDING（正常入队；FAILED 重试须先显式回 PENDING，见
    agent_tasks.execute_agent_task_impl——状态机禁止 FAILED→RUNNING 直跳）；
    或租约已过期的 RUNNING 行（Phase2 Step1：stale 判定唯一权威 =
    ``lease_expires_at``，由心跳线程续租维持；存量 NULL 行回落 updated_at
    旧口径）。认领时换发新 execution_id 并写入租约窗口。
    """
    ensure_schema()
    if stale_running_seconds is None:
        from backend.config.tasks import CELERY_HARD_TASK_TIMEOUT

        stale_running_seconds = CELERY_HARD_TASK_TIMEOUT + 60
    if lease_ttl_seconds is None:
        from backend.config.tasks import TASK_LEASE_TTL_SECONDS

        lease_ttl_seconds = TASK_LEASE_TTL_SECONDS
    execution_id = uuid.uuid4().hex
    sets = ["status = %s", "updated_at = now()",
            "started_at = COALESCE(started_at, now())",
            "execution_id = %s",
            "lease_heartbeat_at = now()",
            "lease_expires_at = now() + (%s || ' seconds')::interval"]
    args: list = [TaskStatus.RUNNING.value, execution_id,
                  str(int(lease_ttl_seconds))]
    if worker:
        sets.append("worker = %s")
        args.append(worker[:128])
    args.extend([
        task_id,
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value, str(int(stale_running_seconds)),
    ])
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET " + ", ".join(sets) + " "
            "WHERE id = %s AND (status = %s OR "
            "(status = %s AND ("
            "(lease_expires_at IS NOT NULL AND lease_expires_at < now()) OR "
            "(lease_expires_at IS NULL AND updated_at < "
            "now() - (%s || ' seconds')::interval))))",
            args,
        )
        return execution_id if cur.rowcount > 0 else None


def renew_lease(task_id: str, execution_id: str, *,
                lease_ttl_seconds: int | None = None) -> bool:
    """续租 = 心跳 + fencing 写（Phase2 Step1）。

    仅当 DB 行仍属于本 execution_id 且状态 RUNNING 时续租成功；rowcount=0
    即租约已被接管/过期——心跳线程据此停止续租，executor 据此退出执行。
    续租同时刷新 updated_at（zombie 最终兜底的"活任务"信号）。
    """
    ensure_schema()
    if lease_ttl_seconds is None:
        from backend.config.tasks import TASK_LEASE_TTL_SECONDS

        lease_ttl_seconds = TASK_LEASE_TTL_SECONDS
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET lease_heartbeat_at = now(), "
            "lease_expires_at = now() + (%s || ' seconds')::interval, "
            "updated_at = now() "
            "WHERE id = %s AND execution_id = %s AND status = %s",
            (str(int(lease_ttl_seconds)), task_id, execution_id,
             TaskStatus.RUNNING.value),
        )
        return cur.rowcount > 0


def check_lease_active(task_id: str, execution_id: str) -> bool:
    """fencing 校验（只读）：本 execution 是否仍是任务的活跃执行者。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM tasks WHERE id = %s AND execution_id = %s "
            "AND status = %s",
            (task_id, execution_id, TaskStatus.RUNNING.value),
        )
        return cur.fetchone() is not None


def reap_zombie_running(
    *,
    threshold_seconds: int,
    limit: int = 100,
    status: TaskStatus = TaskStatus.FAILED,
    error_message: str = "",
    error_type: str = "ZOMBIE_RECONCILED",
    task_id: str | None = None,
) -> list[str]:
    """原子收尸僵尸 RUNNING 任务（2026-09-21 高并发审查 B5）。

    Worker 崩溃且 broker 消息丢失（无 acks_late 重投）时，任务永久卡
    RUNNING。本函数把 ``updated_at`` 停更超过 ``threshold_seconds`` 的
    RUNNING 行原子置为指定终态（beat reconcile → FAILED 可重试；
    管理端强制撤销 → CANCELLED）。

    竞态安全：单条条件 UPDATE，WHERE 里重新校验 status 与心跳——若
    Worker 恰好恢复心跳（updated_at 刷新）或已自行落终态，该行不计入
    返回，绝不误杀活任务。返回被收尸的 task id 列表。
    """
    ensure_schema()
    where = [
        "t.status = 'RUNNING'",
        "t.updated_at < now() - (%s || ' seconds')::interval",
    ]
    # 占位符顺序 = SQL 中出现顺序：where 阈值 → [task_id] → CTE LIMIT →
    # UPDATE 的 status/error_message/error_type（顺序错了会拿状态串去喂 bigint）
    args: list = [str(int(threshold_seconds))]
    if task_id is not None:
        where.append("t.id = %s")
        args.append(task_id)
    args.append(max(1, int(limit)))  # CTE LIMIT %s
    args.extend([status.value, error_message[:2000], error_type[:128]])
    sql = (
        "WITH stale AS ("
        "  SELECT id FROM tasks t WHERE " + " AND ".join(where) +
        "  LIMIT %s"
        ") "
        "UPDATE tasks t SET status = %s, error_message = %s, error_type = %s, "
        "    finished_at = now(), updated_at = now(), "
        "    duration_ms = CASE WHEN t.started_at IS NOT NULL "
        "        THEN (EXTRACT(EPOCH FROM (now() - t.started_at)) * 1000)::int "
        "        ELSE t.duration_ms END "
        "FROM stale WHERE t.id = stale.id RETURNING t.id"
    )
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    return [str(r[0]) for r in rows]


def update_status(task_id: str, status: TaskStatus, *,
                  error_message: str | None = "",
                  error_type: str | None = None,
                  traceback_text: str | None = None,
                  worker: str | None = None,
                  duration_ms: int | None = None,
                  progress: str | None = None,
                  checkpoint_id: str | None = None,
                  output: dict | None = None,
                  celery_task_id: str | None = None,
                  execution_id: str | None = None,
                  retry_exhausted: bool | None = None) -> None:
    """状态迁移 + 可选字段一并更新（单条 UPDATE，避免多写竞态）。

    Phase1 状态机收口：写入前按 ``TaskStatus._legal_transitions`` 白名单
    校验 DB 当前态 → 目标态，非法跳转抛 ``IllegalTaskTransition``（终态
    SUCCESS/CANCELLED 完全封闭，FAILED→RUNNING 禁止——重试须先显式回
    PENDING）。校验与写入之间用 ``WHERE status = <校验时快照>`` 条件更新
    关闭并发窗口：并发写者抢先变更状态时 rowcount=0，按最新状态重新判定。

    Phase2 Step1 执行期 fencing：``execution_id`` 传入时（executor 全部
    执行期写必须传）WHERE 追加 ``AND execution_id = %s``——租约已被接管
    时 rowcount=0 直接抛 ``TaskLeaseLost``，不做并发重判（旧 owner 对
    TaskState 无任何写权）。

    Phase2 Step2 错误口径：``error_message=None`` 表示**不改写**既有错误
    字段（signals retry/failure 兜底不再清掉分类器写入的错误信息）；显式
    ``""`` 仍清空（终态 SUCCESS 收尾时用）。``error_type`` 传 None 不改写、
    传值覆写（runtime 分类词表）。``retry_exhausted`` 仅终态 FAILED 携带。

    时间戳自动治理：
    - RUNNING → started_at（COALESCE 保留首次启动，重试不覆盖）
    - 终态 → finished_at，且 started_at 非空时自动计算 duration_ms
    """
    from backend.models.task import IllegalTaskTransition, TaskLeaseLost

    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"task not found: {task_id}")
        try:
            current = TaskStatus(row[0])
        except ValueError:
            raise ValueError(
                f"task {task_id} 存量状态值非法: {row[0]!r}（状态机拒绝写入）")

        if not current.can_transition_to(status):
            raise IllegalTaskTransition(task_id, current, status)

        sets = ["status = %s", "updated_at = now()"]
        args: list = [status.value]
        if error_message is not None:
            sets.append("error_message = %s")
            args.append(error_message)
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
        if retry_exhausted is not None:
            sets.append("retry_exhausted = %s")
            args.append(bool(retry_exhausted))
        if status == TaskStatus.RUNNING:
            sets.append("started_at = COALESCE(started_at, now())")
        if status.is_terminal():
            sets.append("finished_at = now()")
            # 未显式给耗时时自动补算（执行段耗时，不含排队）
            if duration_ms is None:
                sets.append("duration_ms = CASE WHEN started_at IS NOT NULL THEN "
                            "(EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int "
                            "ELSE duration_ms END")
        if execution_id is not None:
            # fencing 写：不与"并发写者抢先"重判兼容——租约不属于自己就是丢了
            args.extend([task_id, current.value, execution_id])
            cur.execute(
                f"UPDATE tasks SET {', '.join(sets)} "
                f"WHERE id = %s AND status = %s AND execution_id = %s", args)
            if cur.rowcount == 0:
                raise TaskLeaseLost(task_id, execution_id)
            return

        args.extend([task_id, current.value])
        cur.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            f"WHERE id = %s AND status = %s", args)
        if cur.rowcount == 0:
            # 并发写者在校验后抢先变更了状态：按最新状态重判（正常重试一次）
            cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
            latest = cur.fetchone()
            latest_status = TaskStatus(latest[0]) if latest else None
            if latest_status is None or not latest_status.can_transition_to(status):
                raise IllegalTaskTransition(
                    task_id, latest_status or current, status)
            # 重判通过后必须以 latest_status 为 CAS 期望值重发（2026-09-23
            # P1-5 修复：此前复用首次快照 current.value——而走到这里恰恰
            # 说明状态已不等于快照，重试必然 0 行且无人检查，终态写被
            # 静默丢弃，任务卡 PENDING 被重投重跑）。
            args[-1] = latest_status.value
            cur.execute(
                f"UPDATE tasks SET {', '.join(sets)} "
                f"WHERE id = %s AND status = %s", args)
            if cur.rowcount == 0:
                # 二次 CAS 仍被抢占：重判最多一次（防无限循环），绝不静默
                # 成功——按既有并发冲突语义上抛，由调用方按失败处理。
                logger.warning(
                    "[TaskService] %s update_status 二次 CAS 仍被抢占"
                    "（%s → %s），放弃本次写入",
                    task_id, latest_status.value, status.value)
                raise IllegalTaskTransition(task_id, latest_status, status)


def update_progress(task_id: str, node_name: str, progress: str = "",
                    checkpoint_id: str | None = None,
                    *, execution_id: str | None = None) -> bool:
    """节点级进度更新（Worker 每个节点边界调用）。

    checkpoint_id：Phase1 Step2 起执行器传 LangGraph 真实 checkpoint id；
    None 时回落旧口径（= thread_id 占位，仅表达"该 thread 至少有 checkpoint"）。

    Phase2 Step1：execution_id 传入时为 fencing 写（executor 必传）——
    租约被接管的旧 Worker 在此被拒绝，返回 False；调用方必须停止执行。
    """
    ensure_schema()
    if execution_id is not None:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET current_node = %s, progress = %s, "
                "checkpoint_id = COALESCE(%s, thread_id), updated_at = now() "
                "WHERE id = %s AND execution_id = %s AND status = %s",
                (node_name, progress[:500], checkpoint_id, task_id,
                 execution_id, TaskStatus.RUNNING.value),
            )
            return cur.rowcount > 0
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET current_node = %s, progress = %s, "
            "checkpoint_id = COALESCE(%s, thread_id), updated_at = now() "
            "WHERE id = %s",
            (node_name, progress[:500], checkpoint_id, task_id),
        )
    return True


def mark_paused_if_pending(task_id: str, *, progress: str = "队列内暂停") -> bool:
    """队列内暂停：仅当任务仍为 PENDING（未被 Worker 拾取）时原子落 PAUSED。

    Phase1 Step4 特化路径（原生条件 SQL，与状态机白名单 PENDING→PAUSED
    一致，登记为第四条例外通道）：调用方（task_manager.pause_task）在
    rowcount=0（已被拾取变 RUNNING）时必须回落 Redis 标志路径——不能靠
    update_status 的并发重判直写，否则会把正在跑的 Worker 直接标成 PAUSED
    而它无从感知。返回是否落库成功。
    """
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, progress = %s, updated_at = now() "
            "WHERE id = %s AND status = %s",
            (TaskStatus.PAUSED.value, progress[:500], task_id,
             TaskStatus.PENDING.value),
        )
        return cur.rowcount > 0


def claim_for_resume(task_id: str, expected: TaskStatus) -> bool:
    """resume 原子认领：仅当状态仍为 expected 时落 PENDING（回队标记）。

    Phase1 Step5：并发/重复 resume 的仲裁点——两个客户端同时 resume 同一
    任务，条件 UPDATE 只有一个 rowcount=1，败者不得重复入队（执行权由
    Worker 租约最终仲裁，本认领消除重复消息）。expected→PENDING 均为
    状态机白名单合法跳转（PAUSED/WAITING_USER/FAILED→PENDING）。
    """
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, updated_at = now() "
            "WHERE id = %s AND status = %s",
            (TaskStatus.PENDING.value, task_id, expected.value),
        )
        return cur.rowcount > 0


def mark_cancelled_if_status(task_id: str, expected: TaskStatus, *,
                             progress: str = "已取消",
                             error_message: str = "用户取消") -> bool:
    """按预期状态原子落 CANCELLED（Phase1 Step6 队列内/暂停态取消）。

    第五条例外通道（原生条件 SQL，与状态机白名单一致：PENDING/PAUSED/
    WAITING_USER→CANCELLED）。竞态回落由调用方负责——不得经 update_status
    的并发重判直写：把刚被拾取的 RUNNING 行直标 CANCELLED 会让在跑的
    Worker 无从感知（它必须经 Redis 标志在节点边界自行停下）。
    竞态失败（rowcount=0）= 状态已变，调用方应重读后走标志路径。
    终态字段对齐 update_status：finished_at + duration 补算。
    """
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, progress = %s, error_message = %s, "
            "finished_at = now(), updated_at = now(), "
            "duration_ms = CASE WHEN started_at IS NOT NULL THEN "
            "(EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int "
            "ELSE duration_ms END "
            "WHERE id = %s AND status = %s",
            (TaskStatus.CANCELLED.value, progress[:500],
             error_message[:2000], task_id, expected.value),
        )
        return cur.rowcount > 0


def increment_retry(task_id: str) -> int:
    """重试计数 +1，返回新值（Celery self.request.retries 的 DB 侧镜像）。"""
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET retry_count = retry_count + 1, updated_at = now() "
            "WHERE id = %s RETURNING retry_count", (task_id,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def append_checkpoint(task_id: str, node_name: str, state: dict, *,
                      execution_id: str | None = None) -> bool:
    """节点执行完成 → 追加 agent_checkpoints 历史（节点输出快照）。

    Phase2 Step1：execution_id 传入时为 fencing 写——INSERT..SELECT 与
    tasks 行的 execution_id/status 原子绑定，租约被接管的旧 Worker 无法
    追加"幽灵 checkpoint"，返回 False。
    """
    ensure_schema()
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if execution_id is not None:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_checkpoints (task_id, node_name, state_json) "
                "SELECT %s, %s, %s FROM tasks "
                "WHERE tasks.id = %s AND tasks.execution_id = %s "
                "AND tasks.status = %s",
                (task_id, node_name, payload, task_id, execution_id,
                 TaskStatus.RUNNING.value),
            )
            return cur.rowcount > 0
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_checkpoints (task_id, node_name, state_json) "
            "VALUES (%s, %s, %s)",
            (task_id, node_name, payload),
        )
    return True


# ═══════════════════════════════════════════════════
# Stale Recovery（Phase2 Step1：租约过期自动恢复）
# 判定唯一权威 = lease_expires_at（心跳续租维持）；
# lease_expires_at IS NULL 的存量 RUNNING 行回落 updated_at 旧口径。
# ═══════════════════════════════════════════════════

def find_stale_executions(*, grace_seconds: int,
                          legacy_threshold_seconds: int,
                          limit: int = 50,
                          max_age_seconds: int | None = None) -> list[str]:
    """发现租约已过期的 RUNNING 任务（sweeper 扫描入口）。

    grace：比租约过期多等一个宽限，避免与一次在途续租竞态。
    max_age：自动恢复时效上限——updated_at 停更超过该窗口的行不参与
    自动恢复（远古垃圾行交给 zombie 收尸，防止复活旧任务）。
    """
    from backend.config.tasks import TASK_RECOVERY_MAX_AGE_SECONDS

    if max_age_seconds is None:
        max_age_seconds = TASK_RECOVERY_MAX_AGE_SECONDS
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM tasks WHERE status = %s AND updated_at > "
            "now() - (%s || ' seconds')::interval AND ("
            "(lease_expires_at IS NOT NULL AND lease_expires_at < "
            "now() - (%s || ' seconds')::interval) OR "
            "(lease_expires_at IS NULL AND updated_at < "
            "now() - (%s || ' seconds')::interval)) "
            "ORDER BY lease_expires_at NULLS FIRST, updated_at LIMIT %s",
            (TaskStatus.RUNNING.value, str(int(max_age_seconds)),
             str(int(grace_seconds)),
             str(int(legacy_threshold_seconds)), max(1, int(limit))),
        )
        return [str(r[0]) for r in cur.fetchall()]


def claim_stale_for_recovery(task_id: str, *, grace_seconds: int,
                             legacy_threshold_seconds: int,
                             max_recoveries: int,
                             max_age_seconds: int | None = None) -> bool:
    """原子认领 stale 执行做恢复：RUNNING→PENDING（回队标记）+ 计数。

    幂等仲裁点：同一 stale execution 被多个 sweeper 并发扫描，条件 UPDATE
    只有一个 rowcount=1——**同一 stale execution 只能成功触发一次 recovery**
    （重复 recovery 防线）。恢复次数超上限时认领失败，由
    fail_recovered_task 终态收口；时效超上限（max_age）同样不认领。
    """
    from backend.config.tasks import TASK_RECOVERY_MAX_AGE_SECONDS

    if max_age_seconds is None:
        max_age_seconds = TASK_RECOVERY_MAX_AGE_SECONDS
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, recovery_count = recovery_count + 1, "
            "updated_at = now() WHERE id = %s AND status = %s AND updated_at > "
            "now() - (%s || ' seconds')::interval AND ("
            "(lease_expires_at IS NOT NULL AND lease_expires_at < "
            "now() - (%s || ' seconds')::interval) OR "
            "(lease_expires_at IS NULL AND updated_at < "
            "now() - (%s || ' seconds')::interval)) "
            "AND recovery_count < %s",
            (TaskStatus.PENDING.value, task_id, TaskStatus.RUNNING.value,
             str(int(max_age_seconds)), str(int(grace_seconds)),
             str(int(legacy_threshold_seconds)),
             int(max_recoveries)),
        )
        return cur.rowcount > 0


def revert_recovery_claim(task_id: str) -> bool:
    """恢复重投失败回滚：PENDING→RUNNING（租约字段维持过期值，下轮 sweep 重试）。

    注意不清 lease_expires_at——保持"stale"语义，否则任务会以新鲜租约
    假象卡 RUNNING 永远不被再次恢复。
    """
    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, updated_at = now() "
            "WHERE id = %s AND status = %s",
            (TaskStatus.RUNNING.value, task_id, TaskStatus.PENDING.value),
        )
        return cur.rowcount > 0


def fail_recovered_task(task_id: str, *, grace_seconds: int,
                        legacy_threshold_seconds: int, message: str) -> bool:
    """自动恢复次数耗尽的终态收口：stale RUNNING → FAILED(ZOMBIE_RECONCILED)。

    zombie reconcile 的执行体（sweeper 视角）：只在"stale 且
    recovery_count 已达上限"时落 FAILED，可恢复任务永远先走 recovery。
    """
    from backend.config.tasks import TASK_MAX_LEASE_RECOVERIES

    ensure_schema()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, error_type = 'ZOMBIE_RECONCILED', "
            "error_message = %s, finished_at = now(), updated_at = now(), "
            "duration_ms = CASE WHEN started_at IS NOT NULL THEN "
            "(EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int "
            "ELSE duration_ms END "
            "WHERE id = %s AND status = %s AND ("
            "(lease_expires_at IS NOT NULL AND lease_expires_at < "
            "now() - (%s || ' seconds')::interval) OR "
            "(lease_expires_at IS NULL AND updated_at < "
            "now() - (%s || ' seconds')::interval)) "
            "AND recovery_count >= %s",
            (TaskStatus.FAILED.value, message[:2000], task_id,
             TaskStatus.RUNNING.value, str(int(grace_seconds)),
             str(int(legacy_threshold_seconds)), int(TASK_MAX_LEASE_RECOVERIES)),
        )
        return cur.rowcount > 0


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
