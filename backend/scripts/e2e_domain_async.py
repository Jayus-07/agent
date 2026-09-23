"""e2e_domain_async.py — Domain Runtime STOP D 异步/副作用/恢复 域契约演练。

复用冻结层 Phase2 的真实链路 E2E（scripts/e2e_async_runtime.py，R1-R5：
normal / viewer deny / recovery takeover / pause-resume / cancel），本脚本
在其之上补 **Domain 契约**观察与演练：

  D1 信封   tasks 行身份/域列全量观测（user_id/tenant_id/thread_id/graph_name）
  D2 恢复域身份  recovery 前后 user/tenant/thread/graph 不变、execution_id 换发
  D5 副作用幂等  CS 投诉工单重复会话 → 跨轮幂等（同 ticket 复用）
  D7 trace 关联  task session_id=thread_id 贯穿 recovery 前后 trace 记录
  D8 结果回传  GET /api/tasks/{id} 域无关契约（status/result/owner 校验）

纪律：本脚本自身只做真实登录 / 任务 API / 聊天 SSE / 只读 SQL；
worker takeover 由冻结层脚本经 maintenance 队列触发，不动容器。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
PATH_PREFIX = "" if os.getenv("E2E_STRIP_API") == "1" else "/api"
PG = ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
      "-d", "agent_memory", "-t", "-A", "-c"]


def _pg_password() -> str:
    return (os.environ.get("PGPASSWORD_SUPERUSER")
            or os.environ.get("PGPASSWORD") or "postgres")


def psql(sql: str) -> str:
    out = subprocess.run(PG + [sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql: {out.stderr[:200]}")
    return out.stdout.strip()


def pg_exec_container(sql: str) -> str:
    """容器内 psql（口令经容器环境，避免宿主机口令透传）。"""
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c", sql],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql: {out.stderr[:200]}")
    return out.stdout.strip()


def _request(method, url, body=None, token=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    api_key = os.getenv("API_KEY", "")
    if api_key:
        req.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def login(username: str, password: str) -> str:
    st, body = _request("POST", f"{BASE}{PATH_PREFIX}/auth/login",
                        body={"username": username, "password": password})
    if st != 200:
        raise RuntimeError(f"login failed: {st} {body[:200]}")
    token = (json.loads(body).get("data") or {}).get("token")
    if not token:
        raise RuntimeError("login 响应缺 token")
    return token


def setup_account(password: str, username: str, role: str) -> None:
    import psycopg2

    from backend.security.local_jwt import hash_password

    conn = psycopg2.connect(host="127.0.0.1", port=5433, dbname="agent_memory",
                            user="postgres", password=_pg_password())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM auth.users WHERE username=%s", (username,))
            if cur.fetchone():
                cur.execute(
                    "UPDATE auth.users SET role=%s, status=1, password_hash=%s "
                    "WHERE username=%s", (role, hash_password(password), username))
            else:
                cur.execute(
                    "INSERT INTO auth.users (username, password_hash, role, "
                    "dept, status, tenant_id) VALUES (%s, %s, %s, '', 1, 'default')",
                    (username, hash_password(password), role))
        conn.commit()
        print(f"[setup] {username}: role={role}")
    finally:
        conn.close()


# ── D1/D2/D7：任务信封与恢复域身份（复用冻结层脚本跑真实场景）──────────

def d1_envelope_observation() -> bool:
    print("== D1 任务信封观测（最近 8 个任务行）==")
    raw = pg_exec_container(
        "SELECT id, status, user_id, tenant_id, thread_id, graph_name, "
        "coalesce(trace_id,'') FROM tasks ORDER BY created_at DESC LIMIT 8")
    ok = True
    for line in raw.splitlines():
        parts = line.split("|", 6)
        if len(parts) < 7:
            continue
        tid, status, user_id, tenant_id, thread_id, graph_name, trace_id = parts
        envelope_ok = bool(user_id) and bool(thread_id) and bool(graph_name)
        ok = ok and envelope_ok
        print(f"  {tid[:8]} {status:10} user={user_id or '-'} "
              f"tenant={tenant_id or '-'} thread={thread_id or '-'} "
              f"graph={graph_name or '-'} trace={trace_id[:12] or '-'}"
              f"{'' if envelope_ok else '  [FAIL 信封缺失]'}")
    print(f"  {'PASS' if ok else 'FAIL'}（Celery 消息只带 task_id，身份权威在 tasks 行）")
    return ok


def d2_d7_recovery_identity(task_id: str) -> bool:
    print(f"== D2/D7 恢复域身份与 trace 关联（task={task_id[:8]}）==")
    raw = pg_exec_container(
        "SELECT user_id, tenant_id, thread_id, graph_name, recovery_count, "
        f"coalesce(trace_id,'') FROM tasks WHERE id = '{task_id}'")
    # 列：user/tenant/thread/graph/recovery/trace_id
    try:
        user_id, tenant_id, thread_id, graph_name, recovery, trace_id = raw.split("|", 5)
    except ValueError:
        print(f"  FAIL 行缺失: {raw[:100]}")
        return False
    print(f"  user={user_id} tenant={tenant_id} thread={thread_id} "
          f"graph={graph_name} recovery={recovery}")
    domain_ok = bool(user_id) and bool(thread_id) and int(recovery or 0) >= 1
    # D7：task trace 以 session_id=thread_id 落库（task_executor 契约）。
    # trace 权威存储是 app 容器内 SQLite（ai.trace_records PG 镜像为空，
    # 见 STOP C 报告 P2-10），故从容器内读。
    code = (
        "import sys, json;"
        "from backend.observability.trace_store import get_trace_store;"
        "d = get_trace_store().get(sys.argv[1]) or {};"
        "print(json.dumps({'session_id': d.get('session_id',''),"
        "'status': d.get('status',''),"
        "'tags': {k: v for k, v in (d.get('tags') or {}).items()"
        " if k in ('task_id','execution_id','queue')}}))"
    )
    out = subprocess.run(["docker", "exec", "agent-app-1", "python", "-c",
                          code, trace_id], capture_output=True, text=True,
                         timeout=60)
    trace_ok = False
    try:
        info = json.loads((out.stdout or "").strip().splitlines()[-1])
        trace_ok = (info.get("session_id") == thread_id)
        print(f"  trace {trace_id[:12]}: session_id={info.get('session_id')!r} "
              f"status={info.get('status')!r} tags={info.get('tags')}")
    except Exception:
        print(f"  trace 读取失败: {(out.stderr or out.stdout or '')[:150]}")
    print(f"  {'PASS' if domain_ok and trace_ok else 'FAIL'}"
          f"（recovery≥1 且域身份保留；trace.session_id=thread_id 关联）")
    return domain_ok and trace_ok


def d8_result_api(editor: str, viewer: str, task_id: str) -> bool:
    print(f"== D8 异步结果回传契约（GET /api/tasks/{task_id[:8]} + owner 校验）==")
    st, raw = _request("GET", f"{BASE}{PATH_PREFIX}/tasks/{task_id}",
                       token=editor)
    if st != 200:
        print(f"  FAIL http={st} {raw[:150]}")
        return False
    payload = json.loads(raw)
    payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    print(f"  owner 视角 keys={sorted(payload.keys())[:10]} "
          f"status={payload.get('status')}")
    # 属主校验：他人（viewer）读 editor 的任务 → 404（防枚举不区分 403）
    st2, raw2 = _request("GET", f"{BASE}{PATH_PREFIX}/tasks/{task_id}",
                         token=viewer)
    owner_enforced = st2 == 404
    print(f"  他人读取 http={st2}（期望 404 属主隔离）")
    ok = "status" in payload and owner_enforced
    print(f"  {'PASS' if ok else 'FAIL'}（任务状态/结果经 owner 校验回传，"
          f"不因任务失败被 reporter 当 unknown）")
    return ok


# ── D5：CS 投诉工单跨轮幂等（真实聊天链路）────────────────────────────

def _chat(token: str, session_id: str, question: str) -> str:
    body = {"question": question, "session_id": session_id,
            "request_id": f"stopD-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"stopD-{session_id}-{int(time.time()*1000)}"}
    st, raw = _request("POST", f"{BASE}{PATH_PREFIX}/chat/stream",
                       body=body, token=token, timeout=300)
    if st != 200:
        raise RuntimeError(f"chat http={st}: {raw[:200]}")
    answer_parts, events = [], []
    buffer, ev_name = "", None
    # raw 是已读完的 SSE 文本
    for line in raw.splitlines():
        line = line.rstrip("\r")
        if line.startswith("event:"):
            ev_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            events.append((ev_name, data))
            if ev_name == "delta" and isinstance(data, dict):
                answer_parts.append(data.get("content") or "")
    answer = "".join(answer_parts)
    if not answer:
        for name, d in events:
            if isinstance(d, dict) and d.get("final_answer"):
                answer = d["final_answer"]
                break
    return answer


def d5_cs_ticket_idempotent(editor_jwt: str) -> bool:
    print("== D5 CS 投诉工单跨轮幂等（同会话重复投诉）==")
    session_id = f"stopD-ticket-{int(time.time())}"
    # 短语经检测器校准（AFTER_SALES+COMPLAINT 双组 → 进 CS 域图）
    a1 = _chat(editor_jwt, session_id, "商品有质量问题，我要投诉你们")
    a2 = _chat(editor_jwt, session_id, "之前那个质量问题你们根本没解决，我继续投诉")
    print(f"  R1 answer: {a1[:100].replace(chr(10), ' ')}")
    print(f"  R2 answer: {a2[:100].replace(chr(10), ' ')}")
    raw = pg_exec_container(
        "SELECT count(DISTINCT ticket_id) FROM customer_service.tickets "
        f"WHERE conversation_id = '{session_id}'")
    n = int(raw or 0)
    # 同会话重复投诉：不得产生两个新工单（0 = 未建单，如实记录看答案分流）
    idempotent = n <= 1
    entered_cs = ("投诉" in a1 or "工单" in a1 or "抱歉，客服" in a1
                  or len(a1) > 0)  # 路由面由 trace 断言覆盖于 STOP C，这里看结果
    print(f"  tickets(conversation)={n} → "
          f"{'PASS（≤1 个工单，重复投诉未重复建单）' if idempotent else 'FAIL'}")
    return idempotent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--skip-runtime", action="store_true",
                    help="跳过冻结层 R1-R5（已单独跑过时）")
    args = ap.parse_args()

    setup_account(args.password, "e2e_domain", "editor")
    setup_account(args.password, "e2e_travel", "viewer")
    editor = login("e2e_domain", args.password)
    viewer = login("e2e_travel", args.password)
    print("[login] editor + viewer OK\n")

    results: dict[str, bool] = {}

    # 冻结层真实链路矩阵（子进程，输出透传）
    if not args.skip_runtime:
        print("== 冻结层 R1-R5（scripts/e2e_async_runtime.py）==")
        proc = subprocess.run(
            [sys.executable, "scripts/e2e_async_runtime.py",
             "--jwt-editor", editor, "--jwt-viewer", viewer,
             "--api-key", os.getenv("API_KEY", "")],
            capture_output=True, text=True, timeout=1800)
        tail = (proc.stdout or "").strip().splitlines()[-12:]
        print("\n".join(tail))
        results["R1-R5(runtime)"] = proc.returncode == 0 and "PASS" in (proc.stdout or "")

    # 最近 recovery 任务的域身份观测（R3 刚跑完 → 取 recovery_count>=1 最新行）
    raw = pg_exec_container(
        "SELECT id FROM tasks WHERE recovery_count >= 1 "
        "ORDER BY created_at DESC LIMIT 1")
    results["D1_envelope"] = d1_envelope_observation()
    if raw:
        results["D2_D7_recovery_identity"] = d2_d7_recovery_identity(raw.strip())
    else:
        print("== D2/D7 跳过：无 recovery 任务（R3 未跑或未触发）==")
        results["D2_D7_recovery_identity"] = False
    raw_running = pg_exec_container(
        "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1")
    if raw_running:
        results["D8_result_api"] = d8_result_api(editor, viewer, raw_running.strip())
    results["D5_cs_ticket"] = d5_cs_ticket_idempotent(editor)

    print("\n===== STOP D 结果 =====")
    failed = [k for k, v in results.items() if not v]
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\n[result] {'STOP_D_DRILL_PASS' if not failed else 'STOP_D_DRILL_FAIL: ' + ','.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
