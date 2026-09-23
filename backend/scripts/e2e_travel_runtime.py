"""e2e_travel_runtime.py — STOP H 真实 Gateway E2E 驱动（Travel Runtime Acceptance）。

链路（任务书 §13）：Client → APISIX :9080 → JWT → FastAPI → router_node
→ ConversationContextRepository(Redis) → Travel Graph → Postgres Checkpointer → SSE。

用法：
    cd backend
    python scripts/e2e_travel_runtime.py --setup --password <测试密码>   # 幂等建账号(直连5433)
    python scripts/e2e_travel_runtime.py --scenario h_t1 --password <同上>

纪律：只读观察 Redis（GET/EXISTS），除 H-C3 场景外不删任何键；
账号为 STOP H 专用 e2e_travel（viewer），不动既有测试账号。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # .env 提供 API_KEY（X-API-Key 注入）与 PGPASSWORD_SUPERUSER
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
# 经 APISIX 时网关剥 /api（proxy-rewrite）；直连 backend 实例需自带 /api 前缀剥离
PATH_PREFIX = "" if os.getenv("E2E_STRIP_API") == "1" else "/api"
PG_HOST, PG_PORT, PG_DB = "127.0.0.1", 5433, "agent_memory"  # 显式 5433


def _request(method: str, url: str, body: dict | None = None,
             token: str | None = None, timeout: float = 300):
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


def setup_account(password: str, username: str = "e2e_travel") -> None:
    """幂等创建/更新 STOP H 专用账号（viewer；直连权威库 5433）。"""
    import psycopg2

    from backend.security.local_jwt import hash_password

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user="postgres",
                            password=os.environ.get("PGPASSWORD_SUPERUSER",
                                                    "postgres"))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM auth.users WHERE username=%s", (username,))
            if cur.fetchone():
                cur.execute(
                    "UPDATE auth.users SET role='viewer', status=1, "
                    "password_hash=%s WHERE username=%s",
                    (hash_password(password), username))
                print(f"[setup] {username}: 密码重置/角色确认 viewer")
            else:
                cur.execute(
                    "INSERT INTO auth.users (username, password_hash, role, "
                    "dept, status, tenant_id) VALUES (%s, %s, 'viewer', '', 1, 'default')",
                    (username, hash_password(password)))
                print(f"[setup] {username}: 创建(viewer)")
        conn.commit()
    finally:
        conn.close()


def login(username: str, password: str) -> str:
    """经 APISIX :9080 真实登录拿 JWT（链路①：login 本身就走网关）。"""
    t0 = time.time()
    status, body = _request("POST", f"{BASE}{PATH_PREFIX}/auth/login",
                            body={"username": username, "password": password})
    dt = (time.time() - t0) * 1000
    if status != 200:
        raise RuntimeError(f"login failed: {status} {body[:200]}")
    token = (json.loads(body).get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"login 响应缺 token: {body[:200]}")
    print(f"[login] OK via APISIX ({dt:.0f}ms) jwt_len={len(token)}")
    return token


def jwt_claims(token: str) -> dict:
    """解 JWT payload（不验证签名——仅用于取身份构造观测 key）。"""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def chat_stream(token: str, session_id: str, question: str, *,
                domain_hint: str | None = None, timeout: float = 300) -> dict:
    """POST /api/chat/stream 经 APISIX；解析 SSE 帧序列。

    Returns: {status, content_type, events[], answer, done_count, error_count,
              travel_frames, latency_ms}
    """
    body = {"question": question, "session_id": session_id,
            "request_id": f"stopH-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"stopH-{session_id}-{int(time.time()*1000)}"}
    if domain_hint:
        body["domain_hint"] = domain_hint
    req = urllib.request.Request(f"{BASE}{PATH_PREFIX}/chat/stream",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    # X-API-Key：与前端 BFF 注入同源（.env API_KEY），值不打印
    api_key = os.getenv("API_KEY", "")
    if api_key:
        req.add_header("X-API-Key", api_key)
    # 直连实例时模拟 APISIX gateway-auth 注入（H-M1 实例级验证专用；
    # 主验收链路仍经 APISIX 真实注入，不伪造）
    tenant_hdr = os.getenv("E2E_TENANT_HDR", "")
    user_hdr = os.getenv("E2E_USER_HDR", "")
    if tenant_hdr:
        req.add_header("X-Tenant-Id", tenant_hdr)
    if user_hdr:
        req.add_header("X-User-Id", user_hdr)
    req.add_header("Accept", "text/event-stream")

    events: list[dict] = []
    answer_parts: list[str] = []
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
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
                        if ev_name in ("delta", None) and isinstance(data, dict) \
                                and data.get("content"):
                            answer_parts.append(data["content"])
                        elif ev_name is None and isinstance(data, dict) \
                                and data.get("type") == "delta":
                            answer_parts.append(data.get("content") or "")
    except urllib.error.HTTPError as e:
        return {"status": e.code, "content_type": "",
                "error_body": e.read().decode("utf-8", "replace")[:400],
                "events": [], "answer": "", "latency_ms": (time.time()-t0)*1000}

    answer = "".join(answer_parts)
    if not answer:  # 兜底：done 帧或独立 answer 帧里找 final_answer
        for ev in events:
            d = ev.get("data")
            if isinstance(d, dict) and d.get("final_answer"):
                answer = d["final_answer"]
                break
    done_count = sum(1 for e in events if e["event"] == "done"
                     or (isinstance(e["data"], dict)
                         and e["data"].get("type") in ("done", "end")))
    return {
        "status": status, "content_type": ctype, "events": events,
        "answer": answer, "done_count": done_count,
        "latency_ms": (time.time() - t0) * 1000,
        "types": [e["event"] or (e["data"].get("type") if isinstance(
            e["data"], dict) else None) for e in events][:40],
    }


def redis_context_key(tenant: str, user: str, conversation: str) -> str:
    enc = lambda s: urllib.parse.quote(s or "", safe="")  # noqa: E731
    return f"agent:conversation_context:v1:{enc(tenant)}:{enc(user)}:{enc(conversation)}"


def redis_get_json(key: str) -> dict | None:
    """只读 GET（走 backend 同一 Redis 实例 db0；禁止任何写/删）。"""
    import redis as redis_lib

    from backend.config.redis import REDIS_URL

    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3, socket_connect_timeout=3)
    raw = client.get(key)
    return json.loads(raw) if raw else None


def print_context_snapshot(ctx: dict | None, tag: str) -> None:
    if ctx is None:
        print(f"[redis][{tag}] context key MISSING")
        return
    pending = ctx.get("travel_pending")
    print(f"[redis][{tag}] version={ctx.get('version')} "
          f"seq={ctx.get('travel_run_seq')} run={ctx.get('travel_run_id')} "
          f"stage={ctx.get('travel_stage')!r} "
          f"pending={(pending or {}).get('question_id')}:{(pending or {}).get('requested_slots')} "
          f"dest={ctx.get('destination')!r} days={ctx.get('days')} "
          f"budget={ctx.get('budget_cny')} lodging={ctx.get('lodging')!r} "
          f"avoid={ctx.get('avoid')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="e2e_travel")
    parser.add_argument("--scenario", default="",
                        help="h_t1|h_t2|h_t3|h_t4|h_t5|h_t6|h_t7|h_mc1|h_mc2|snap")
    parser.add_argument("--conversation", default="")
    args = parser.parse_args()

    if args.setup:
        setup_account(args.password, args.username)
        return 0

    token = login(args.username, args.password)
    claims = jwt_claims(token)
    tenant = str(claims.get("tenant") or claims.get("tenant_id") or "")
    user = str(claims.get("userId") or claims.get("user_id") or "")  # 后端口径 userId
    print(f"[jwt] tenant={tenant[:6]}… user={user[:6]}… claims={sorted(claims)}")

    conv = args.conversation or f"stopH-{args.scenario or 'x'}-{int(time.time())}"
    print(f"[conv] {conv}")

    ctx_key = redis_context_key(tenant, user, conv)
    print(f"[redis] context key = {ctx_key}")

    # 输入口径（任务书 §30 认可）：缺槽/续跑轮用「明确 travel 信号」输入
    # （prefilter 保守契约：口语短句归三层 Router，短答案续跑归 resolver）
    scenarios = {
        # H-T1: 缺槽轮（规划大阪行程 → pending days）
        "h_t1": [("帮我规划厦门的行程", None)],  # 大阪不在P0种子城市(福州/厦门/杭州)
        # H-T2: 纯短答案 CONTINUE（STOP F 最初的路由断点场景）
        "h_t2": [("三天", None)],
        # H-T3: PATCH 预算
        "h_t3": [("预算改成6万", None)],
        # H-T4: 局部排除 ≠ CANCEL(种子景点)
        "h_t4": [("不去鼓浪屿了", None)],  # 种子景点等价替换海游馆(大阪无种子数据)
        # H-T5: 显式 CANCEL
        "h_t5": [("这次旅行不规划了", None)],
        # H-T6: Cancel 后 NEW_RUN（信号明确 → prefilter 确定性命中）
        "h_t6": [("重新规划杭州两天的行程", None)],
        # H-T7: 跨轮住宿 PATCH + 预算
        "h_t7": [("住西湖附近", None), ("预算改成5000", None)],
        # H-C2: backend restart 后 checkpoint 恢复
        "h_c2": [("改成四天", None)],
        # H-C3: context 丢失 + checkpoint hit（明确 travel 信号）
        "h_c3": [("大阪行程改成5天", None)],
        # H-C4: checkpoint 丢失 + context hit
        "h_c4": [("改成3天", None)],
        # H-C5: both missing → fresh
        "h_c5": [("重新规划杭州两天的行程", None)],
        # H-M1: backend-B 跨实例 CONTINUE
        "h_m1": [("三天", None)],
    }
    turns = scenarios.get(args.scenario)
    if not turns:
        print(f"unknown scenario {args.scenario}")
        return 2

    for q, hint in turns:
        print(f"\n===== [{args.scenario}] U: {q}")
        result = chat_stream(token, conv, q, domain_hint=hint)
        print(f"[sse] status={result['status']} ctype={result['content_type']} "
              f"done={result.get('done_count')} latency={result.get('latency_ms'):.0f}ms "
              f"frames={len(result.get('events', []))}")
        if result["status"] != 200:
            print(f"[sse] ERROR body: {result.get('error_body')}")
            return 1
        print(f"[answer] {(result.get('answer') or '')[:180]}")
        print_context_snapshot(redis_get_json(ctx_key), "after")

    return 0


if __name__ == "__main__":
    sys.exit(main())
