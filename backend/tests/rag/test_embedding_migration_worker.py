"""真实 HTTP/PG/任务租约验收，供应商为隔离的确定性 HTTP 服务。"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from backend.infra.async_utils import run_on_worker_loop
from backend.infra.llm import registry_store, specialized
from backend.rag.indexing import embedding_migration as migration
from backend.rag.vectorstore.pgvector_store import EMBEDDING_DIM
from backend.services import task_service
from backend.services.task_state import TaskManager
from backend.shared.crypto import encrypt_secret, fingerprint, last4
from backend.tasks.index_tasks import rebuild_embeddings_task
from backend.tests.rag._migration_residue_guard import cleanup_residue, orphan_binding_warning
from backend.tests.rag.test_embedding_migration import data  # noqa: F401


@pytest.fixture
def worker_env(data):
    stores, _ = data
    # llm_usage 表：OBS_DB_PG_TABLE_PREFIX 前缀重定向下单例可能持有旧表名
    # （pop 重载后外层单例仍指旧实例），且 CI 隔离库无人建表（本地主栈表
    # 由运行栈建，写入裸名恒存在——v22 run 41344790496 gw1 实证写入
    # pgtest_biz_gw1_llm_usage UndefinedTable、查询裸表恒 None）。重置
    # 单例按当前 env 重建 + 幂等建表。
    from backend.observability import llm_usage_store as _lus

    _lus._llm_usage_store = None
    _lus.get_llm_usage_store()._init_db()
    # 跨进程自愈：上次硬杀崩溃的残留（劫持的绑定/死端口供应商）会让运行栈
    # embedding 全挂，且不能在坏状态上叠测。fresh 残留属并行活跃会话，守卫
    # 自带时间阈值不会误杀；孤儿绑定说明上次清理被打断，fail-fast 交人工。
    orphan = orphan_binding_warning()
    # 「缺少 embedding 角色行」在 CI 隔离库（无运行栈写入）是正常初始态：
    # 本 fixture 会 INSERT 绑定并在 finally 恢复（old_role=None 路径），
    # 不是崩溃残留——残留的形态是「绑定指向不存在/测试残留的模型」。
    # v19 run 41329142940 CI 库无 seed 行 6 例被误拦实证。
    if orphan and "缺少 embedding 角色行" not in orphan:
        pytest.fail(f"embedding 绑定处于残留状态，先人工修复再跑本验收：{orphan}")
    # stale_only=False：CI 隔离库无并行会话，migration-test 租户的未终态
    # 重建任务（占 biz_id='global' 全局锁）必为本套件上一轮残留——不限
    # stale 收口，否则上一轮未收尾的任务让本轮撞 ModelConfigConflict
    # （v26 run 41354669257 实证；本地共享栈由 guard 默认 stale 保护）。
    cleanup_residue(stale_only=False)
    state = SimpleNamespace(items=0, fail_after=None)

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            texts = payload["input"]
            if state.fail_after is not None and state.items + len(texts) > state.fail_after:
                self.send_response(503)
                self.end_headers()
                return
            state.items += len(texts)
            body = {"data": [{"index": i, "object": "embedding", "embedding":
                              [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)}
                             for i, _ in enumerate(texts)],
                    "model": payload["model"], "object": "list",
                    "usage": {"prompt_tokens": len(texts) * 4, "total_tokens": len(texts) * 4}}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider_id = "migration-test-" + uuid.uuid4().hex[:8]
    url = f"http://127.0.0.1:{server.server_address[1]}"
    key = uuid.uuid4().hex
    with task_service._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT model_name FROM llm_model_role_bindings WHERE role='embedding'")
        old_role = cur.fetchone()
        cur.execute(
            "INSERT INTO llm_providers(id,display_name,driver,base_url,network_scope,billing) "
            "VALUES (%s,%s,'openai',%s,'private','local')", (provider_id, provider_id, url),
        )
        cur.execute(
            "INSERT INTO llm_models(name,provider_id,display_name,model_kind) "
            "VALUES ('new',%s,'隔离嵌入模型','embedding')", (provider_id,),
        )
        cur.execute(
            "INSERT INTO llm_provider_credentials(provider_id,key_cipher,key_fingerprint,key_last4) "
            "VALUES (%s,%s,%s,%s)", (provider_id, encrypt_secret(key), fingerprint(key), last4(key)),
        )
        cur.execute(
            "INSERT INTO llm_model_role_bindings(role,model_name) VALUES ('embedding','new') "
            "ON CONFLICT (role) DO UPDATE SET model_name='new'"
        )
    assert run_on_worker_loop(registry_store.refresh_registry())
    binding = specialized.registered_binding("embedding", "new")
    fp = specialized.embedding_fingerprint(binding)
    try:
        yield stores, state, fp
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        with task_service._conn() as conn, conn.cursor() as cur:
            if old_role:
                cur.execute("UPDATE llm_model_role_bindings SET model_name=%s WHERE role='embedding'", old_role)
            else:
                cur.execute("DELETE FROM llm_model_role_bindings WHERE role='embedding'")
            # provider_id 限定：并行会话可能同建 name='new' 的模型，只删自己那行。
            cur.execute("DELETE FROM llm_models WHERE name='new' AND provider_id=%s", (provider_id,))
            assert cur.rowcount == 1, f"模型行删除失败（{provider_id}）——检查并发/残留"
            cur.execute("DELETE FROM llm_provider_credentials WHERE provider_id=%s", (provider_id,))
            assert cur.rowcount == 1, f"凭据行删除失败（{provider_id}）"
            cur.execute("DELETE FROM llm_providers WHERE id=%s", (provider_id,))
            assert cur.rowcount == 1, f"供应商行删除失败（{provider_id}）"
            # 收尾断言（可发现性）：恢复失败若静默，运行栈 embedding 会热切换
            # 到死端口——teardown 必须显式失败而不是放过。
            cur.execute("SELECT model_name FROM llm_model_role_bindings WHERE role='embedding'")
            restored = cur.fetchone()
        expected = old_role  # old_role 即 fetchone() 结果（单值元组或 None）
        assert restored == expected, (
            f"embedding 绑定未恢复（当前={restored!r}，期望={expected!r}）——"
            "运行栈 embedding 仍指向测试残留，立即人工修复")
        run_on_worker_loop(registry_store.refresh_registry())


def make_task(fp):
    return TaskManager.create(
        "migration-test-admin", "隔离全量重嵌验证", tenant_id="migration-test",
        graph_name="rag_index", biz_type="embedding_rebuild", biz_id="global",
        extra_input={"embedding_rebuild_kwargs": {"model_name": "new", "fingerprint": fp}},
    )


def test_real_worker_http_accounting_and_atomic_publication(worker_env, monkeypatch):
    monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "true")
    stores, state, fp = worker_env
    record = make_task(fp)
    migration.mark_required()
    result = rebuild_embeddings_task.run("new", fp, record.id)
    assert result["vectors"] == 3
    assert state.items == 3
    saved = task_service.get_task(record.id)
    assert saved.status.value == "SUCCESS"
    assert saved.output["model"] == "new"
    assert saved.output["trace_id"]
    with task_service._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT total_tokens,pricing_source,usage_source,billed_cost_cny "
                    "FROM llm_usage WHERE trace_id=%s AND model='new'",
                    (saved.output["trace_id"],))
        usage = cur.fetchone()
        assert usage is not None and usage[0] > 0
        assert usage[1:3] == ("local_inference", "estimated")
        assert usage[3] == 0
    for store in stores:
        assert store.get_index_meta()["embeddingModel"] == "new"
        assert store.get_index_meta()["modelRevision"] == fp
    # 重复投递同一任务不得再发请求、再扣模型用量。
    assert rebuild_embeddings_task.run("new", fp, record.id)["skipped"] is True
    assert state.items == 3


def test_provider_failure_keeps_old_vectors_and_reports_failed_task(worker_env):
    stores, state, fp = worker_env
    stores[0].add_texts([f"额外文档{i}" for i in range(7)],
                        [{"doc_id": f"extra{i}"} for i in range(7)])
    state.fail_after = 8
    record = make_task(fp)
    migration.mark_required()
    with pytest.raises(Exception):
        rebuild_embeddings_task.run("new", fp, record.id)
    assert task_service.get_task(record.id).status.value == "FAILED"
    assert state.items == 8
    for store in stores:
        meta = store.get_index_meta()
        assert meta["status"] == "rebuild_required"
        assert meta["embeddingModel"] == "old"
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {migration._table()} "
                    "WHERE position('::cand:' in collection)=0 "
                    "AND (embedding::real[])[1]=1")
        assert cur.fetchone()[0] == 10
    # 同一任务恢复时复用已付费的八条候选，不从头重复调用。
    state.fail_after = None
    restored = rebuild_embeddings_task.run("new", fp, record.id)
    assert restored["vectors"] == 10
    assert state.items == 10
    assert task_service.get_task(record.id).status.value == "SUCCESS"


def test_published_but_task_completion_failed_recovers_without_new_model_calls(worker_env, monkeypatch):
    """向量提交后状态库暂时失败，恢复只对齐状态，不再生成或收费。"""
    stores, state, fp = worker_env
    record = make_task(fp)
    migration.mark_required()
    original = TaskManager.mark_success

    def unavailable(*args, **kwargs):
        raise RuntimeError("模拟提交后的任务状态写入失败")

    monkeypatch.setattr(TaskManager, "mark_success", unavailable)
    with pytest.raises(RuntimeError, match="任务状态"):
        rebuild_embeddings_task.run("new", fp, record.id)
    assert state.items == 3
    assert stores[0].get_index_meta()["status"] == "ready"
    monkeypatch.setattr(TaskManager, "mark_success", original)
    # 使用正式恢复流程前先重置失败执行的租约，模拟回收后的重投。
    with task_service._conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET status='FAILED',execution_id='',lease_expires_at=NULL WHERE id=%s",
                    (record.id,))
    restored = rebuild_embeddings_task.run("new", fp, record.id)
    assert restored["publication_recovered"] is True
    assert restored["vectors"] == 3
    assert state.items == 3
    assert task_service.get_task(record.id).status.value == "SUCCESS"


def test_trace_finish_failure_does_not_report_published_vectors_as_failed(worker_env, monkeypatch):
    from backend.observability.tracer import trace_collector

    stores, state, fp = worker_env
    record = make_task(fp)
    migration.mark_required()
    monkeypatch.setattr(trace_collector, "finish", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("模拟 Trace 存储故障")))
    result = rebuild_embeddings_task.run("new", fp, record.id)
    assert result["trace_status"] == "finish_failed"
    assert task_service.get_task(record.id).status.value == "SUCCESS"
    assert stores[0].get_index_meta()["status"] == "ready"
    assert state.items == 3


def test_metered_cloud_protocol_uses_same_rebuild_and_price_ledger(worker_env, monkeypatch):
    """云端协议/价格使用隔离 HTTP 替身，不花真实云端费用。"""
    from backend.services.model_config import ModelConfigService

    monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "true")
    stores, state, fp = worker_env
    binding = specialized.registered_binding("embedding", "new")
    version = "migration-test-" + uuid.uuid4().hex
    with task_service._conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE llm_providers SET billing='metered' WHERE id=%s", (binding.provider_id,))
        cur.execute(
            "INSERT INTO model_price(model_name,component,dimension,price_per_unit,"
            "unit,currency,price_table_version,source,effective_from,approval_status,reviewer_1,reviewer_2) "
            "VALUES ('new','embedding','input',1,'per_1m_tokens','CNY',%s,"
            "'provider-page',now(),'approved','test-reviewer','system')", (version,),
        )
    try:
        assert run_on_worker_loop(registry_store.refresh_registry())
        preview = run_on_worker_loop(ModelConfigService().preview_embedding_change("new"))
        assert preview["billing"] == "metered" and preview["dimension"] == EMBEDDING_DIM
        record = make_task(fp)
        migration.mark_required()
        result = rebuild_embeddings_task.run("new", fp, record.id)
        assert result["vectors"] == 3
        assert state.items == 4
        with task_service._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT price_version,total_tokens,billed_cost_cny,usage_source "
                        "FROM llm_usage WHERE trace_id=%s AND model='new'", (result["trace_id"],))
            usage = cur.fetchone()
            assert usage[0] == version and usage[1] > 0 and usage[2] > 0
            assert usage[3] == "estimated"
        assert stores[2].get_index_meta()["embeddingModel"] == "new"
    finally:
        with task_service._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE model_price SET effective_to=now() WHERE price_table_version=%s", (version,))


def test_real_broker_worker_and_repeated_submit_are_idempotent(worker_env, tmp_path):
    """真实消息消费验证，只连接本轮独立 Redis/PG，子进程结束后退出。"""
    stores, state, fp = worker_env
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["LOG_FILE"] = str(tmp_path / "worker-app.log")
    output = tmp_path / "worker-console.log"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with output.open("w", encoding="utf-8") as log:
        worker = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "backend.tasks.celery_app",
             "worker", "--pool=solo", "--concurrency=1", "-Q", "rag_index",
             "--without-gossip", "--without-mingle", "--without-heartbeat",
             "-n", "migration-test@%h", "--loglevel=WARNING"],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        try:
            key = uuid.uuid4().hex
            submitted = run_on_worker_loop(migration.submit_rebuild(
                "new", fp, True, "migration-test-admin", "migration-test", key,
            ))
            deadline = time.monotonic() + 60
            saved = None
            while time.monotonic() < deadline:
                saved = task_service.get_task(submitted["taskId"])
                if saved.status.is_terminal():
                    break
                assert worker.poll() is None, output.read_text(encoding="utf-8")[-3000:]
                time.sleep(0.2)
            assert saved.status.value == "SUCCESS", output.read_text(encoding="utf-8")[-3000:]
            assert saved.queue == "rag_index"
            assert stores[2].get_index_meta()["embeddingModel"] == "new"
            # 即使出现更新的任务，原幂等键也必须定位原任务，不能再产生费用。
            newer = make_task(fp)
            TaskManager.mark_failed(newer.id, error_message="隔离测试的后续任务")
            again = run_on_worker_loop(migration.submit_rebuild(
                "new", fp, True, "migration-test-admin", "migration-test", key,
            ))
            assert again["taskId"] == submitted["taskId"] and again["joined"]
            assert state.items == 3
        finally:
            worker.terminate()
            worker.wait(timeout=15)
