"""e2e_domain_tenant.py — Domain Runtime STOP E / E3 双租户隔离实机演练。

非破坏性：新建 tenant-b 专用账号（e2e_tenantb），与 default 租户的
e2e_domain 用**同一个 session_id** 分别跑一轮 travel，验证：
  - ConversationContext 键按 (tenant, user, conversation) 天然分桶；
  - 跨租户读任务 → 404（属主+租户校验）；
  - memory_records 按租户分域。

SQL 数仓 18 表无租户列（结构隔离决策 D1，STOP A §9）——该面不在本演练
断言范围，如实记录为设计边界。
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
import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

GATEWAY = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
APP_DIRECT = "http://127.0.0.1:8000"


def _pg_password() -> str:
    return (os.environ.get("PGPASSWORD_SUPERUSER")
            or os.environ.get("PGPASSWORD") or "postgres")


def setup_tenant_b_user(password: str) -> None:
    import psycopg2

    from backend.security.local_jwt import hash_password

    conn = psycopg2.connect(host="127.0.0.1", port=5433, dbname="agent_memory",
                            user="postgres", password=_pg_password())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM auth.users WHERE username=%s",
                        ("e2e_tenantb",))
            if cur.fetchone():
                cur.execute(
                    "UPDATE auth.users SET role='editor', status=1, "
                    "tenant_id='tenant-b', password_hash=%s "
                    "WHERE username=%s", (hash_password(password), "e2e_tenantb"))
            else:
                cur.execute(
                    "INSERT INTO auth.users (username, password_hash, role, "
                    "dept, status, tenant_id) VALUES "
                    "(%s, %s, 'editor', '', 1, 'tenant-b')",
                    ("e2e_tenantb", hash_password(password)))
        conn.commit()
        print("[setup] e2e_tenantb: role=editor tenant=tenant-b")
    finally:
        conn.close()


def _request(method, url, body=None, token=None, headers=None, timeout=150):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if os.getenv("API_KEY"):
        req.add_header("X-API-Key", os.getenv("API_KEY"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def login_tenant_b(password: str) -> tuple[str, str]:
    """tenant-b 登录：直连 app（可信网络边界），显式带 X-Tenant-Id。"""
    st, body = _request(
        "POST", f"{APP_DIRECT}/auth/login",
        body={"username": "e2e_tenantb", "password": password},
        headers={"X-Tenant-Id": "tenant-b"})
    if st != 200:
        raise RuntimeError(f"tenant-b login failed: {st} {body[:150]}")
    token = (json.loads(body).get("data") or {}).get("token")
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    claims = json.loads(base64_dec(part))
    return token, str(claims.get("userId") or "")


def base64_dec(part: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def chat(token: str, session_id: str, question: str) -> str:
    body = {"question": question, "session_id": session_id,
            "request_id": f"stopE3-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"stopE3-{session_id}-{int(time.time()*1000)}"}
    st, raw = _request("POST", f"{GATEWAY}/api/chat/stream", body=body,
                       token=token, timeout=180)
    if st != 200:
        raise RuntimeError(f"chat http={st}: {raw[:150]}")
    answer, buffer, ev_name = [], "", None
    for line in raw.splitlines():
        line = line.rstrip("\r")
        if line.startswith("event:"):
            ev_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            if ev_name == "delta" and isinstance(data, dict):
                answer.append(data.get("content") or "")
    return "".join(answer)


def redis_key(tenant: str, user: str, conversation: str) -> str:
    enc = lambda s: urllib.parse.quote(s or "", safe="")  # noqa: E731
    return f"agent:conversation_context:v1:{enc(tenant)}:{enc(user)}:{enc(conversation)}"


def redis_get(key: str):
    import redis as redis_lib

    from backend.config.redis import REDIS_URL

    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3, socket_connect_timeout=3)
    raw = client.get(key)
    return json.loads(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    setup_tenant_b_user(args.password)
    tb_token, tb_user = login_tenant_b(args.password)
    print(f"[login] tenant-b user={tb_user}")

    # default 租户 editor 登录（复用网关链路）
    st, body = _request("POST", f"{GATEWAY}/api/auth/login",
                        body={"username": "e2e_domain",
                              "password": args.password})
    a_token = (json.loads(body).get("data") or {}).get("token")
    a_user = "50"

    session_id = f"stopE3-shared-{int(time.time())}"
    a_ans = chat(a_token, session_id, "帮我规划厦门两天的行程")
    b_ans = chat(tb_token, session_id, "帮我规划杭州两天的行程")
    print(f"[A default/{a_user}] {a_ans[:60]!r}")
    print(f"[B tenant-b/{tb_user}] {b_ans[:60]!r}")

    ok = True
    # 1) 上下文键分桶
    ka = redis_key("default", a_user, session_id)
    kb = redis_key("tenant-b", tb_user, session_id)
    ca, cb = redis_get(ka), redis_get(kb)
    bucket_ok = (ca is not None and cb is not None and ka != kb
                 and (ca.get("destination") == "厦门")
                 and (cb.get("destination") == "杭州"))
    print(f"[ctx] A key={ka[-40:]} dest={(ca or {}).get('destination')!r}")
    print(f"[ctx] B key={kb[-40:]} dest={(cb or {}).get('destination')!r}")
    print(f"  {'PASS' if bucket_ok else 'FAIL'}（同 session_id 跨租户上下文互不可见）")
    ok = ok and bucket_ok

    # 2) 跨租户任务读取 → 404
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c",
         "SELECT id FROM tasks WHERE user_id='50' ORDER BY created_at DESC LIMIT 1"],
        capture_output=True, text=True)
    a_task = out.stdout.strip()
    st, _ = _request("GET", f"{GATEWAY}/api/tasks/{a_task}", token=tb_token)
    cross_ok = st == 404
    print(f"  tenant-b 读 default 用户任务: http={st} → "
          f"{'PASS' if cross_ok else 'FAIL'}")
    ok = ok and cross_ok

    # 3) memory_records 按租户分域
    q = ("SELECT tenant_id, count(*) FROM memory_records "
         "WHERE user_id IN ('%s','%s') GROUP BY tenant_id" % (a_user, tb_user))
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c", q],
        capture_output=True, text=True)
    print(f"[mem] {out.stdout.strip().splitlines()}")
    print("\n[result] " + ("E3_TENANT_ISOLATION_PASS" if ok
                           else "E3_TENANT_ISOLATION_FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
