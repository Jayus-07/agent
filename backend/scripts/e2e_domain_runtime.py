"""e2e_domain_runtime.py — Domain Runtime STOP C 跨域会话连续性真实链路驱动。

链路（任务书 C8/C9）：Client → APISIX :9080 → JWT → FastAPI /chat/stream
→ router_node 判域 → 域图/主图 → SSE；旁路只读观测：Redis
ConversationContext（active_domain / travel pending）、PostgreSQL 5433
（chat_messages / checkpoints / ai.trace_records）。

用法：
    cd backend
    python scripts/e2e_domain_runtime.py --setup --password <测试密码>   # 幂等建账号(直连5433)
    python scripts/e2e_domain_runtime.py --run --password <同上>          # C1-C9 单会话 10 轮矩阵

纪律：只读观测（Redis GET / PG SELECT）；专用账号 e2e_domain（editor，
sql.read 用），不动既有测试账号；身份/密码经 env/参数，不落 secret。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()                       # backend/ 或仓库根的 .env
    load_dotenv(dotenv_path="../.env")  # 仓库根（脚本从 backend/ 运行时）
except ImportError:
    pass

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
PATH_PREFIX = "" if os.getenv("E2E_STRIP_API") == "1" else "/api"
PG_HOST, PG_PORT, PG_DB = "127.0.0.1", 5433, "agent_memory"  # 显式 5433


def _request(method, url, body=None, token=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _pg_password() -> str:
    """超级用户口令：PGPASSWORD_SUPERUSER 优先，回退根 .env 的 PGPASSWORD。"""
    return (os.environ.get("PGPASSWORD_SUPERUSER")
            or os.environ.get("PGPASSWORD") or "postgres")


def setup_account(password: str, username: str = "e2e_domain") -> None:
    """幂等创建/更新专用账号（editor：C1 矩阵需要 sql.read）。"""
    import psycopg2

    from backend.security.local_jwt import hash_password

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user="postgres",
                            password=_pg_password())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM auth.users WHERE username=%s", (username,))
            if cur.fetchone():
                cur.execute(
                    "UPDATE auth.users SET role='editor', status=1, "
                    "password_hash=%s WHERE username=%s",
                    (hash_password(password), username))
                print(f"[setup] {username}: 密码重置/角色确认 editor")
            else:
                cur.execute(
                    "INSERT INTO auth.users (username, password_hash, role, "
                    "dept, status, tenant_id) VALUES (%s, %s, 'editor', '', 1, 'default')",
                    (username, hash_password(password)))
                print(f"[setup] {username}: 创建(editor)")
        conn.commit()
    finally:
        conn.close()


def login(username: str, password: str) -> str:
    status, body = _request("POST", f"{BASE}{PATH_PREFIX}/auth/login",
                            body={"username": username, "password": password})
    if status != 200:
        raise RuntimeError(f"login failed: {status} {body[:200]}")
    token = (json.loads(body).get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"login 响应缺 token: {body[:200]}")
    print(f"[login] OK via APISIX jwt_len={len(token)}")
    return token


def jwt_claims(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def chat_stream(token: str, session_id: str, question: str,
                timeout: float = 300) -> dict:
    body = {"question": question, "session_id": session_id,
            "request_id": f"stopC-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"stopC-{session_id}-{int(time.time()*1000)}"}
    req = urllib.request.Request(f"{BASE}{PATH_PREFIX}/chat/stream",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    api_key = os.getenv("API_KEY", "")
    if api_key:
        req.add_header("X-API-Key", api_key)
    req.add_header("Accept", "text/event-stream")

    events: list[dict] = []
    answer_parts: list[str] = []
    trace_id = ""
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if status != 200:
                return {"status": status, "events": [], "answer": "",
                        "done_count": 0, "trace_id": "",
                        "error_body": resp.read().decode("utf-8", "replace")[:300]}
            buffer, ev_name = "", None
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")
                    if line.startswith("event:"):
                        ev_name = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        payload = line.split(":", 1)[1].strip()
                        try:
                            data = json.loads(payload)
                        except ValueError:
                            data = payload
                        events.append({"event": ev_name, "data": data})
                        if ev_name == "delta" and isinstance(data, dict):
                            answer_parts.append(data.get("content") or "")
                        elif ev_name == "done" and isinstance(data, dict):
                            trace_id = data.get("trace_id") or trace_id
    except urllib.error.HTTPError as e:
        return {"status": e.code, "events": [], "answer": "", "done_count": 0,
                "trace_id": "",
                "error_body": e.read().decode("utf-8", "replace")[:300]}

    answer = "".join(answer_parts)
    if not answer:
        for ev in events:
            d = ev.get("data")
            if isinstance(d, dict) and d.get("final_answer"):
                answer = d["final_answer"]
                break
    done_count = sum(1 for e in events if e["event"] == "done")
    error_count = sum(1 for e in events if e["event"] == "error")
    return {"status": status, "events": events, "answer": answer,
            "done_count": done_count, "error_count": error_count,
            "trace_id": trace_id, "latency_ms": (time.time() - t0) * 1000}


# ── 只读观测 ──────────────────────────────────────────────────────

def redis_context(tenant: str, user: str, conversation: str) -> dict | None:
    import redis as redis_lib
    import urllib.parse

    from backend.config.redis import REDIS_URL

    key = ("agent:conversation_context:v1:"
           + urllib.parse.quote(tenant or "", safe="")
           + ":" + urllib.parse.quote(user or "", safe="")
           + ":" + urllib.parse.quote(conversation or "", safe=""))
    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3, socket_connect_timeout=3)
    raw = client.get(key)
    return json.loads(raw) if raw else None


def pg_query(sql: str, params: tuple) -> list[dict]:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user="postgres", password=_pg_password())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── C1/C9 会话矩阵 ────────────────────────────────────────────────

def trace_route(trace_id: str) -> list[str]:
    """从 app 容器 SQLite trace store 读该轮真实执行节点（路由观测权威面）。"""
    code = (
        "import sys, json;"
        "from backend.observability.trace_store import get_trace_store;"
        "d = get_trace_store().get(sys.argv[1]) or {};"
        "g = d.get('graph') or {};"
        "print(json.dumps([n.get('id') for n in (g.get('nodes') or [])]))"
    )
    out = subprocess.run(
        ["docker", "exec", "agent-app-1", "python", "-c", code, trace_id],
        capture_output=True, text=True, timeout=60)
    try:
        return json.loads((out.stdout or "").strip().splitlines()[-1])
    except Exception:
        return []


# (turn_id, question, expected_node in trace graph.nodes / None=只查非域图, note)
# 短语口径：CS 全局入口需 ≥2 规则组命中（CS_RULE_MIN_HITS=2，precision-tuned；
# 单信号查询按设计交主路由拒答兜底——2026-09-24 实测与工作区代码逐字一致，
# 非部署漂移）。expected_node 取 trace graph.nodes 的节点 id。
TURNS = [
    ("T1_general", "你好，请介绍一下平台都能做什么",
     {"general_chat", "reporter"}, "General 入口"),
    ("T2_rag", "根据知识库文档说明客户生命周期阶段",
     {"reporter", "skill_executor"}, "RAG/clarify 兜底（禁域图）"),
    ("T3_sql", "查一下最近一个月每天的销售额",
     {"skill_executor", "workflow_executor"}, "SQL/数据主链（禁域图）"),
    ("T4_cs", "帮我查一下这个订单的物流进度，到底什么时候到",
     {"cs_graph_node"}, "CS 切入（双组强规则）"),
    ("T5_cs_followup", "那这个订单的物流进度到底怎么样了",
     {"cs_graph_node"}, "CS follow-up 连续性"),
    ("T6_travel", "帮我规划厦门的行程",
     {"travel_graph_node"}, "切 Travel"),
    ("T7_travel_followup", "三天",
     {"travel_graph_node"}, "Travel 补槽 follow-up"),
    ("T8_cs_return", "订单里的行程单怎么退款",
     {"cs_graph_node"}, "CS 回归（Travel→CS）"),
    ("T9_ambiguous", "那这个怎么办",
     None, "模糊回指（禁 travel/selection 域图）"),
    ("T10_selection", "给宠物零食做一次智能选品",
     {"selection_funnel_graph_node"}, "切 Selection"),
]


def run_matrix(token: str, tenant: str, user: str, session_id: str) -> bool:
    ok = True
    route_log: list[dict] = []
    for turn_id, question, expect_nodes, note in TURNS:
        result = chat_stream(token, session_id, question)
        answer = (result.get("answer") or "").strip()
        trace_id = result.get("trace_id") or ""
        nodes = trace_route(trace_id) if trace_id else []
        hard_ok = (result["status"] == 200 and result.get("done_count") == 1
                   and len(answer) >= 8)
        route_ok = True
        if expect_nodes is not None:
            route_ok = bool(nodes) and bool(set(nodes) & expect_nodes)
        else:
            # T9：模糊回指不得被随机切进 travel/selection 域图（C4）
            route_ok = bool(nodes) and not (
                {"travel_graph_node", "selection_funnel_graph_node",
                 "cs_graph_node"} & set(nodes))
        ctx = redis_context(tenant, user, session_id) or {}
        active = ctx.get("active_domain") or ""
        turn_ok = hard_ok and route_ok
        ok = ok and turn_ok
        print(f"[{'PASS' if turn_ok else 'FAIL'}] {turn_id} ({note}) "
              f"http={result['status']} done={result.get('done_count')} "
              f"err={result.get('error_count', '?')} nodes={nodes} "
              f"active_domain={active!r} latency={result.get('latency_ms', 0):.0f}ms")
        print(f"       answer: {answer[:110].replace(chr(10), ' ')}")
        route_log.append({"turn": turn_id, "question": question,
                          "nodes": nodes, "active_domain": active,
                          "ok": turn_ok})

    # ── C9 落库一致性核查（只读）───────────────────────────────
    print("\n===== [C9] 落库一致性核查 =====")
    rows = pg_query(
        "SELECT role, content FROM chat_messages WHERE session_id=%s "
        "ORDER BY id ASC", (session_id,))
    cs_leak = [r["content"] for r in rows
               if "这个订单的物流进度" in (r.get("content") or "")
               or "订单里的行程单怎么退款" in (r.get("content") or "")]
    print(f"[db] chat_messages rows={len(rows)} （CS 轮豁免：预期不含 T4/T5/T8 提问原文）")
    if cs_leak:
        print(f"[db][FAIL] CS 轮写入了 chat_messages（隔离被破坏）: {cs_leak[:1]}")
        ok = False
    else:
        print("[db][PASS] CS 轮未写入 chat_messages")

    ck = pg_query(
        "SELECT thread_id, count(*) AS n FROM checkpoints "
        "WHERE thread_id LIKE %s GROUP BY thread_id",
        (f"travel:{session_id}",))
    print(f"[db] travel checkpoints: {[(r['thread_id'], r['n']) for r in ck] or '无'}")

    mem = pg_query(
        "SELECT count(*) AS n FROM memory_records "
        "WHERE user_id=%s AND tenant_id=%s", (user, tenant))
    print(f"[db] memory_records(user)={mem[0]['n']}（L3 异步管线，信息性观测）")

    print("\n===== [route log] =====")
    for r in route_log:
        print(json.dumps(r, ensure_ascii=False))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="e2e_domain")
    parser.add_argument("--session", default="")
    args = parser.parse_args()

    if args.setup:
        setup_account(args.password, args.username)
        return 0
    if not args.run:
        print("use --setup or --run")
        return 2

    token = login(args.username, args.password)
    claims = jwt_claims(token)
    tenant = str(claims.get("tenant") or claims.get("tenant_id") or "")
    user = str(claims.get("userId") or claims.get("user_id") or "")
    print(f"[jwt] tenant={tenant!r} user={user!r} claims={sorted(claims)}")

    session_id = args.session or f"stopC-{int(time.time())}"
    print(f"[session] {session_id}")
    ok = run_matrix(token, tenant, user, session_id)
    print(f"\n[result] {'STOP_C_MATRIX_PASS' if ok else 'STOP_C_MATRIX_FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
