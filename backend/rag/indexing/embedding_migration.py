"""全库重新嵌入：复用已入库文本、模型计量、rag_index 队列及任务租约。

候选向量完整构建后才一次性发布；失败不改旧向量、不解除待重建状态。
此操作是平台级维护，入口仅允许 super_admin，不能用于租户自行重写全库。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
from contextlib import contextmanager

from backend.rag.vectorstore.pgvector_store import (
    EMBEDDING_DIM, PgVectorKnowledgeStore, _META_DDL_DONE, _ensure_meta_table, _get_vector_pool,
    _meta_table_name,
)


def _table() -> str:
    prefix = os.getenv("VECTOR_PG_TABLE_PREFIX", "")
    if prefix and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prefix):
        raise ValueError("向量表前缀不合法")
    return prefix + "rag_vectors"


@contextmanager
def connection():
    """复用正式向量连接池及事务语义。"""
    pool = _get_vector_pool()
    conn = pool.getconn()
    discard = False
    try:
        from pgvector.psycopg2 import register_vector

        register_vector(conn)
        yield conn
        conn.commit()
    except Exception:
        _META_DDL_DONE.discard(_meta_table_name())
        try:
            conn.rollback()
        except Exception:
            discard = True
        raise
    finally:
        pool.putconn(conn, close=discard)


def storage_snapshot() -> dict:
    """读取真实列维度和集合范围，不依据环境变量猜测已有库的维度。"""
    table = _table()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid=to_regclass(%s) AND attname='embedding'",
            (table,),
        )
        row = cur.fetchone()
        if row is None:
            return {"dimension": EMBEDDING_DIM, "vectors": 0, "collections": []}
        match = re.fullmatch(r"vector\((\d+)\)", row[0])
        if match is None:
            raise ValueError("向量列不是受支持的定长 vector，需先完成存储迁移")
        if int(match[1]) != EMBEDDING_DIM:
            raise ValueError("VECTOR_PG_DIM 与数据库实际维度不一致，请先修正配置，不会自动改表")
        cur.execute(
            f"SELECT collection, count(*) FROM {table} "
            "WHERE position('::cand:' in collection)=0 GROUP BY collection "
            "ORDER BY collection"
        )
        collections = [{"collection": name, "vectors": count}
                       for name, count in cur.fetchall()]
        cur.execute("SELECT to_regclass(%s)", (_meta_table_name(),))
        metadata = {}
        if cur.fetchone()[0] is not None:
            cur.execute(f"SELECT collection,embedding_provider,embedding_model,"
                        f"embedding_dimension,model_revision,status "
                        f"FROM {_meta_table_name()}")
            metadata = {row[0]: row[1:] for row in cur.fetchall()}
        for item in collections:
            meta = metadata.get(item["collection"]) or (None, None, None, None, None)
            item.update(provider=meta[0], model=meta[1],
                        dimension=meta[2], fingerprint=meta[3], status=meta[4])
    return {"dimension": int(match[1]),
            "vectors": sum(item["vectors"] for item in collections),
            "collections": collections}


def read_index_metadata() -> list[dict]:
    """管理端也读取真实向量库，支持元数据库与向量数据库分开部署。"""
    with connection() as conn, conn.cursor() as cur:
        metadata: dict[str, dict] = {}
        vector_collections: set[str] = set()
        cur.execute("SELECT to_regclass(%s)", (_meta_table_name(),))
        if cur.fetchone()[0] is not None:
            cur.execute(
                f"SELECT collection,embedding_provider,embedding_model,"
                "embedding_dimension,model_revision,index_version,built_at,"
                f"updated_at,status FROM {_meta_table_name()} "
                "WHERE collection<>'__embedding_control__' "
                "AND position('::cand:' in collection)=0"
            )
            columns = [item[0] for item in cur.description]
            metadata = {
                str(row["collection"]): row
                for row in (dict(zip(columns, values)) for values in cur.fetchall())
            }

        cur.execute("SELECT to_regclass(%s)", (_table(),))
        if cur.fetchone()[0] is not None:
            cur.execute(
                f"SELECT DISTINCT collection FROM {_table()} "
                "WHERE position('::cand:' in collection)=0 "
            )
            vector_collections = {str(row[0]) for row in cur.fetchall()}

        collections = sorted(set(metadata) | vector_collections)
        return [
            {
                "collection": collection,
                "embedding_provider": metadata.get(collection, {}).get("embedding_provider"),
                "embedding_model": metadata.get(collection, {}).get("embedding_model"),
                "embedding_dimension": metadata.get(collection, {}).get("embedding_dimension"),
                "model_revision": metadata.get(collection, {}).get("model_revision"),
                "index_version": metadata.get(collection, {}).get("index_version"),
                "built_at": metadata.get(collection, {}).get("built_at"),
                "updated_at": metadata.get(collection, {}).get("updated_at"),
                "status": metadata.get(collection, {}).get("status"),
                "has_vectors": collection in vector_collections,
            }
            for collection in collections
        ]


def validate_vectors(vectors: list, count: int, dimension: int) -> None:
    """200 响应不等于有效向量，拒绝数量/维度错误和 NaN/Inf。"""
    if not isinstance(vectors, (list, tuple)) or len(vectors) != count or any(
        not isinstance(vector, (list, tuple)) or len(vector) != dimension or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) for value in vector
        ) or not any(vector) for vector in vectors
    ):
        raise ValueError(f"嵌入模型输出无效：要求 {count} 条 {dimension} 维有限数值向量")


def mark_required() -> None:
    """在切换角色之前关闭整个旧向量空间，包含路由和历史集合。"""
    table = _table()
    with connection() as conn, conn.cursor() as cur:
        _ensure_meta_table(conn)
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('rag-embedding-publication'))")
        if not cur.fetchone()[0]:
            raise ValueError("文档正在发布，请稍后重试模型切换")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('rag-embedding-write'))")
        cur.execute(
            f"INSERT INTO {_meta_table_name()} "
            "(collection,embedding_provider,embedding_model,embedding_dimension,status) "
            "VALUES ('__embedding_control__','unknown','unknown',%s,'rebuild_required') "
            "ON CONFLICT (collection) DO UPDATE SET "
            "status='rebuild_required',updated_at=now()", (EMBEDDING_DIM,),
        )
        cur.execute("SELECT to_regclass(%s)", (table,))
        if cur.fetchone()[0] is None:
            return
        cur.execute(
            f"INSERT INTO {_meta_table_name()} "
            "(collection,embedding_provider,embedding_model,embedding_dimension,status) "
            f"SELECT DISTINCT collection,'unknown','unknown',%s,'rebuild_required' "
            f"FROM {table} WHERE position('::cand:' in collection)=0 "
            "ON CONFLICT (collection) DO UPDATE SET "
            "status='rebuild_required',updated_at=now()",
            (EMBEDDING_DIM,),
        )
        cur.execute(
            f"UPDATE {_meta_table_name()} SET status='rebuild_required',updated_at=now() "
            "WHERE position('::cand:' in collection)=0"
        )


def _assert_target(model_name: str, fingerprint: str) -> None:
    """每批和发布前确认目标没有被另一位管理员改变。"""
    from backend.config import model_roles
    from backend.infra.llm import specialized

    if model_roles.resolve_effective("embedding")["value"] != model_name:
        raise ValueError("重建期间嵌入模型已改变，请按最新配置重新提交")
    binding = specialized.registered_binding("embedding", model_name)
    if specialized.embedding_fingerprint(binding) != fingerprint:
        raise ValueError("重建期间供应商或模型参数已改变，已停止发布")


def activate_empty_space(model_name: str, fingerprint: str, provider: str,
                         dimension: int) -> None:
    """没有任何旧向量时可以直接启用，但仍给旧 worker 设置身份围栏。"""
    with connection() as conn, conn.cursor() as cur:
        _ensure_meta_table(conn)
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('rag-embedding-publication'))")
        if not cur.fetchone()[0]:
            raise ValueError("文档正在发布，请稍后重试")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('rag-embedding-write'))")
        cur.execute("SELECT to_regclass(%s)", (_table(),))
        if cur.fetchone()[0] is not None:
            cur.execute(f"SELECT 1 FROM {_table()} "
                        "WHERE position('::cand:' in collection)=0 LIMIT 1")
            if cur.fetchone() is not None:
                raise ValueError("预检后出现新向量，请重新检查并确认重建")
        cur.execute(
            f"INSERT INTO {_meta_table_name()} "
            "(collection,embedding_provider,embedding_model,embedding_dimension,model_revision,status) "
            "VALUES ('__embedding_control__',%s,%s,%s,%s,'ready') "
            "ON CONFLICT (collection) DO NOTHING", (provider, model_name, dimension, fingerprint),
        )
        cur.execute(
            f"UPDATE {_meta_table_name()} SET embedding_provider=%s,embedding_model=%s,"
            "embedding_dimension=%s,model_revision=%s,status='ready',updated_at=now() "
            "WHERE position('::cand:' in collection)=0",
            (provider, model_name, dimension, fingerprint),
        )


async def submit_rebuild(model_name: str, fingerprint: str | None, confirmed: bool,
                         user_id: str, tenant_id: str, request_key: str) -> dict:
    """先持久化任务再入队，全局维护任务不允许无记录降级。"""
    from sqlalchemy import text

    from backend.infra.llm import registry_store
    from backend.memory.database import get_session
    from backend.models.task import TaskStatus
    from backend.services import task_service
    from backend.services.model_config import ModelConfigConflict
    from backend.services.task_state import TaskManager
    from backend.tasks.index_tasks import rebuild_embeddings_task
    from backend.tasks.queue_router import log_route, resolve_for_workflow

    if not confirmed or not fingerprint or not user_id or not tenant_id:
        raise ValueError("请确认重建范围与费用，并提供有效的平台管理员身份")
    if not await registry_store.refresh_registry():
        raise RuntimeError("模型注册表不可用，未创建重建任务")
    _assert_target(model_name, fingerprint)
    kwargs = {"model_name": model_name, "fingerprint": fingerprint}
    # 首次初始化的 DDL 不能放在另一连接持有 tasks 读锁之后，否则自阻塞。
    task_service.ensure_schema()
    async for session in get_session():
        await session.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext('embedding-role-change'))"
        ))
        current = await session.execute(text(
            "SELECT model_name FROM llm_model_role_bindings WHERE role='embedding'"
        ))
        row = current.first()
        if row is None or row[0] != model_name:
            raise ModelConfigConflict("嵌入模型已变化，请刷新后重新提交")
        previous = await session.execute(text(
            "SELECT id FROM tasks WHERE biz_type='embedding_rebuild' AND biz_id='global' "
            "AND input->>'rebuild_request_key'=:key ORDER BY created_at DESC LIMIT 1"
        ), {"key": request_key})
        previous_id = previous.first()
        if previous_id:
            prior = task_service.get_task(previous_id[0])
            if prior is None:
                raise RuntimeError("原重建任务暂不可读取，请稍后重试")
            if (prior.input or {}).get("embedding_rebuild_kwargs") != kwargs:
                raise ModelConfigConflict("同一幂等键不能提交不同的重建目标")
            return {"taskId": prior.id, "status": prior.status.value, "joined": True}
        latest = task_service.get_latest_biz_task("embedding_rebuild", "global")
        if latest and latest.status in (
            TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED,
        ):
            if (latest.input or {}).get("embedding_rebuild_kwargs") != kwargs:
                raise ModelConfigConflict("另一项向量重建尚未结束，请先处理该任务")
            return {"taskId": latest.id, "status": latest.status.value, "joined": True}
        mark_required()
        record = TaskManager.create(
            user_id, "全库重新生成向量", tenant_id=tenant_id,
            graph_name="rag_index", biz_type="embedding_rebuild", biz_id="global",
            extra_input={"embedding_rebuild_kwargs": kwargs,
                         "rebuild_request_key": request_key},
        )
        route = resolve_for_workflow("rag_index")
        try:
            result = rebuild_embeddings_task.apply_async(
                kwargs={**kwargs, "db_task_id": record.id}, queue=route.physical_queue,
            )
            task_service.mark_queued(record.id, result.id, queue=route.physical_queue)
            log_route(route, dispatch_type="initial", task_id=record.id,
                      celery_task_name="tasks.rebuild_embeddings")
        except Exception:
            TaskManager.mark_failed(record.id, error_message="重建任务入队失败，检索仍待重建")
            raise RuntimeError("任务队列不可用，重建未成功提交") from None
        await session.commit()
        return {"taskId": record.id, "status": "PENDING", "joined": False}
    raise RuntimeError("任务存储不可用")


def _publish(conn, stage: str, model_name: str, fingerprint: str,
             provider: str, dimension: int, total: int, task_id: str = "") -> None:
    """锁定向量表后校验完整快照，再原子替换所有向量和索引身份。"""
    table = _table()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('rag-embedding-publication'))")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('rag-embedding-write'))")
        cur.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
        cur.execute(
            f"SELECT count(*) FROM {table} WHERE position('::cand:' in collection)=0"
        )
        if cur.fetchone()[0] != total:
            raise ValueError("重建期间文档集合发生变化，旧索引保留，请重新提交")
        cur.execute(
            f"SELECT count(*) FROM {table} source JOIN {table} stage "
            "ON source.id=stage.metadata->>'source_id' "
            "AND source.collection=stage.metadata->>'source_collection' "
            "AND source.content=stage.content "
            "AND source.metadata::text=stage.metadata->>'source_metadata' "
            "WHERE stage.collection=%s AND position('::cand:' in source.collection)=0",
            (stage,),
        )
        if cur.fetchone()[0] != total:
            raise ValueError("候选向量不完整或源文本已变化，已拒绝发布")
        cur.execute(
            f"UPDATE {table} source SET embedding=stage.embedding, "
            "metadata=source.metadata || jsonb_build_object("
            "'embedding_model',%s::text,'embedding_fingerprint',%s::text,"
            "'embedding_task_id',%s::text) "
            f"FROM {table} stage WHERE stage.collection=%s "
            "AND source.id=stage.metadata->>'source_id'",
            (model_name, fingerprint, task_id, stage),
        )
        cur.execute(
            f"UPDATE {_meta_table_name()} SET embedding_provider=%s,embedding_model=%s,"
            "embedding_dimension=%s,model_revision=%s,status='ready',"
            "index_version=index_version+1,built_at=now(),updated_at=now() "
            "WHERE position('::cand:' in collection)=0",
            (provider, model_name, dimension, fingerprint),
        )
        cur.execute(f"DELETE FROM {table} WHERE collection=%s", (stage,))
        cur.execute(f"DELETE FROM {_meta_table_name()} WHERE collection=%s", (stage,))
        # 与向量同事务保留提交凭证，防止提交后、TaskState 成功前崩溃导致重复收费。
        # 使用候选命名空间，既有集合统计与检索入口均不会把它当作业务集合。
        cur.execute(
            f"INSERT INTO {_meta_table_name()} "
            "(collection,embedding_provider,embedding_model,embedding_dimension,model_revision,status) "
            "VALUES (%s,%s,%s,%s,%s,'ready') ON CONFLICT (collection) DO NOTHING",
            (f"{stage}:published:{total}", provider, model_name, dimension, fingerprint),
        )


def _published_count(stage: str, model_name: str, fingerprint: str) -> int | None:
    """读取同任务的原子提交凭证；没有凭证不能推测发布成功。"""
    with connection() as conn, conn.cursor() as cur:
        _ensure_meta_table(conn)
        cur.execute(
            f"SELECT collection FROM {_meta_table_name()} WHERE collection LIKE %s "
            "AND embedding_model=%s AND model_revision=%s AND status='ready'",
            (stage + ":published:%", model_name, fingerprint),
        )
        row = cur.fetchone()
        return int(row[0].rsplit(":", 1)[1]) if row else None


def run_rebuild(task_id: str, model_name: str, fingerprint: str) -> dict:
    """候选构建可以重试，只有全量成功且仍持有租约才能发布。"""
    from backend.config.rag import (
        EMBEDDING_REBUILD_BATCH_SIZE, EMBEDDING_REBUILD_REQUEST_TIMEOUT,
    )
    from backend.core.request_context import RequestContext
    from backend.infra.llm import registry_store
    from backend.observability.tracer import trace_collector
    from backend.rag.embedding_singleton import get_registered_embedding
    from backend.services import task_service
    from backend.tasks.execution_context import get_execution

    record = task_service.get_task(task_id)
    execution = get_execution()
    if record is None or execution is None or execution[0] != task_id:
        raise RuntimeError("全量重建必须通过正式任务租约执行")
    stage = "embedding::cand:reembed-" + hashlib.sha256(task_id.encode()).hexdigest()[:24]
    published = _published_count(stage, model_name, fingerprint)
    if published is not None:
        return {"terminal": "done", "vectors": published, "model": model_name,
                "fingerprint": fingerprint, "publication_recovered": True}
    # 每批刷新配置，不能让常驻 worker 仅凭旧快照发布。
    from backend.infra.async_utils import run_on_worker_loop

    if not run_on_worker_loop(registry_store.refresh_registry()):
        raise RuntimeError("模型注册表刷新失败，重建未执行")
    _assert_target(model_name, fingerprint)
    from backend.infra.llm.pricing import clear_price_cache

    clear_price_cache()
    embedding = get_registered_embedding(
        model_name, request_timeout=EMBEDDING_REBUILD_REQUEST_TIMEOUT,
        batch_size=EMBEDDING_REBUILD_BATCH_SIZE, max_retries=0,
    )
    info = storage_snapshot()
    dimension = info["dimension"]
    table = _table()
    store = PgVectorKnowledgeStore(stage, embedding)
    trace = trace_collector.start("全库重新生成向量", session_id=task_id)
    RequestContext(session_id=task_id, user_id=record.user_id,
                   tenant_id=record.tenant_id, trace=trace).bind()
    started = time.monotonic()
    processed = 0
    last_id = ""
    try:
        while True:
            if not run_on_worker_loop(registry_store.refresh_registry()):
                raise RuntimeError("重建期间模型注册表不可用，已停止")
            _assert_target(model_name, fingerprint)
            if not task_service.check_lease_active(task_id, execution[1]):
                from backend.models.task import TaskLeaseLost

                raise TaskLeaseLost(task_id)
            with connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT id,collection,content,metadata::text FROM {table} "
                    "WHERE id>%s AND position('::cand:' in collection)=0 "
                    "ORDER BY id LIMIT 8", (last_id,),
                )
                rows = cur.fetchall()
            if not rows:
                break
            with connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT metadata->>'source_id',content,metadata->>'source_metadata' "
                    f"FROM {table} WHERE collection=%s "
                    "AND metadata->>'source_id'=ANY(%s) "
                    "AND metadata->>'embedding_fingerprint'=%s",
                    (stage, [row[0] for row in rows], fingerprint),
                )
                cached = {row[0]: row[1:] for row in cur.fetchall()}
            missing = [row for row in rows if cached.get(row[0]) != (row[2], row[3])]
            if missing:
                from backend.infra.llm.budget import bind_request_budget

                # 每批有界预算；整个任务仍受用户/租户持久费用额度限制。
                bind_request_budget(f"{task_id}:embedding-batch:{processed}",
                                    user_id=record.user_id, tenant_id=record.tenant_id)
                span = trace_collector.start_span(
                    "embedding_rebuild_batch", name="批量重新生成向量", type="embedding",
                    input={"vector_ids": [row[0] for row in missing],
                           "parse_reused": True, "task_id": task_id},
                )
                try:
                    texts = [row[2] for row in missing]
                    vectors = embedding.embed_documents(texts)
                    validate_vectors(vectors, len(texts), dimension)
                    metas = [{"source_id": row[0], "source_collection": row[1],
                              "source_metadata": row[3]} for row in missing]
                    ids = [stage + ":" + hashlib.sha256(row[0].encode()).hexdigest()
                           for row in missing]
                    store._upsert_rows(store._texts_to_rows(texts, metas, vectors, ids))
                    trace_collector.end_span(span, metrics={"vectors": len(texts)})
                except Exception:
                    trace_collector.end_span(span, status="error")
                    raise
            processed += len(rows)
            last_id = rows[-1][0]
            if not task_service.update_progress(
                task_id, "embedding_rebuild", f"已生成 {processed}/{info['vectors']} 条向量",
                execution_id=execution[1],
            ):
                raise RuntimeError("重建任务租约已失效")
        if not run_on_worker_loop(registry_store.refresh_registry()):
            raise RuntimeError("发布前模型配置不可确认")
        _assert_target(model_name, fingerprint)
        # 持有任务行锁直到向量事务提交，租约接管不能穿过发布窗口。
        with (task_service._conn() as task_conn,
              task_conn.transaction(), task_conn.cursor() as task_cur):
            task_cur.execute(
                "SELECT 1 FROM tasks WHERE id=%s AND execution_id=%s "
                "AND status='RUNNING' FOR UPDATE", (task_id, execution[1]),
            )
            if task_cur.fetchone() is None:
                raise RuntimeError("任务执行权已改变，已拒绝发布")
            task_cur.execute(
                "SELECT model_name FROM llm_model_role_bindings "
                "WHERE role='embedding' FOR SHARE"
            )
            selected = task_cur.fetchone()
            if selected is None or selected[0] != model_name:
                raise ValueError("发布前模型绑定已改变")
            task_cur.execute("SELECT id FROM llm_providers WHERE id=%s FOR SHARE",
                             (embedding._provider_id,))
            task_cur.execute("SELECT name FROM llm_models WHERE name=%s FOR SHARE",
                             (model_name,))
            task_cur.execute("SELECT role FROM llm_specialized_model_bindings "
                             "WHERE role='embedding' FOR SHARE")
            if not run_on_worker_loop(registry_store.refresh_registry()):
                raise RuntimeError("发布前注册表读取失败")
            _assert_target(model_name, fingerprint)
            with connection() as conn:
                _publish(conn, stage, model_name, fingerprint,
                         embedding._provider_id, dimension, processed, task_id)
        trace_status = "ok"
        try:
            trace_collector.finish(trace, "全库向量已重建并发布",
                                   int((time.monotonic()-started)*1000), model_name)
        except Exception:
            from backend.shared.logger import logger

            trace_status = "finish_failed"
            logger.exception("重建已发布，但 Trace 收尾失败 task_id=%s trace_id=%s", task_id, trace.id)
        return {"terminal": "done", "vectors": processed, "trace_id": trace.id,
                "model": model_name, "fingerprint": fingerprint, "trace_status": trace_status}
    except Exception:
        trace.status = "error"
        try:
            trace_collector.finish(trace, "重建失败，旧向量保留，检索仍待重建",
                                   int((time.monotonic()-started)*1000), model_name)
        except Exception:
            from backend.shared.logger import logger

            logger.exception("重建失败且 Trace 收尾失败 task_id=%s trace_id=%s", task_id, trace.id)
        raise
    finally:
        close = getattr(embedding, "close", None)
        if callable(close):
            close()
