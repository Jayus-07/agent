"""e2e_tenant_collision.py — Platform Readiness STOP C 跨租户碰撞复现（C2/C3/C4）。

两个租户使用**相同 session_id**（任务书明确要求的构造）：
  A=default/e2e_domain  B=tenant-b/e2e_tenantb  session=stopC3-shared-*

验证面：
  C4 session 收养：B 用 A 的 session 聊天 → 是否读到 A 的 L2 历史（chat_messages）
  C3 checkpoint 碰撞：A/B 都走 travel → travel:{conv} 同一 LangGraph thread →
     A 续问「改成五天」时 brief 是否被 B 的目的地污染

用法：
    cd backend && PYTHONPATH=.. python scripts/e2e_tenant_collision.py \
        --password <pwd> [--base http://127.0.0.1:9080] [--session xxx]
隔离实例复验（修复后）：--base http://127.0.0.1:8001 --direct
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
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

_user_id, _tenant_id = "", ""


def _request(method, url, body=None, token=None, headers=None, timeout=180):
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


def login_gateway(username: str, password: str) -> str:
    st, body = _request("POST", "http://127.0.0.1:9080/api/auth/login",
                        body={"username": username, "password": password})
    if st != 200:
        raise RuntimeError(f"login {username} failed: {st} {body[:120]}")
    return (json.loads(body).get("data") or {}).get("token")


def login_direct_tenant(username: str, password: str, tenant: str) -> str:
    """非 default 租户登录：直连 app + 显式可信租户头（网关 auth 路由
    无条件注入 default 租户——单租户部署现实，见 apisix.yaml:56 注释）。"""
    st, body = _request("POST", "http://127.0.0.1:8000/auth/login",
                        body={"username": username, "password": password},
                        headers={"X-Tenant-Id": tenant})
    if st != 200:
        raise RuntimeError(f"direct login {username}@{tenant} failed: {st} {body[:120]}")
    return (json.loads(body).get("data") or {}).get("token")


def chat(base: str, token: str, session_id: str, question: str,
         direct: bool = False, actor: dict | None = None) -> str:
    prefix = "" if direct else "/api"
    body = {"question": question, "session_id": session_id,
            "request_id": f"stopC3-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"stopC3-{session_id}-{int(time.time()*1000)}"}
    headers = {}
    if direct and actor:
        headers = {"X-Auth-Type": "jwt", "X-User-Id": actor["user_id"],
                   "X-Tenant-Id": actor["tenant_id"] or "default",
                   "X-User-Roles": "editor"}
    st, raw = _request("POST", f"{base}{prefix}/chat/stream", body=body,
                       token=token, headers=headers)
    if st != 200:
        raise RuntimeError(f"chat http={st}: {raw[:150]}")
    answer, buffer, ev = [], "", None
    for line in raw.splitlines():
        line = line.rstrip("\r")
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            try:
                d = json.loads(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            if ev == "delta" and isinstance(d, dict):
                answer.append(d.get("content") or "")
    return "".join(answer)


def db(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c", sql],
        capture_output=True, text=True)
    return out.stdout.strip()


def main() -> int:
    global _user_id, _tenant_id
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:9080")
    ap.add_argument("--direct", action="store_true")
    ap.add_argument("--session", default="")
    args = ap.parse_args()

    tok_a = login_gateway("e2e_domain", args.password)
    tok_b = login_direct_tenant("e2e_tenantb", args.password, "tenant-b")

    def claims_of(tok: str) -> dict:
        part = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    ca, cb = claims_of(tok_a), claims_of(tok_b)
    actor_a = {"user_id": str(ca.get("userId")),
               "tenant_id": str(ca.get("tenant_id") or "default")}
    actor_b = {"user_id": str(cb.get("userId")),
               "tenant_id": str(cb.get("tenant_id") or "default")}
    _user_id, _tenant_id = actor_a["user_id"], actor_a["tenant_id"]
    print(f"[login] A={actor_a['tenant_id']}/{actor_a['user_id']}  "
          f"B={actor_b['tenant_id']}/{actor_b['user_id']}")

    session = args.session or f"stopC3-shared-{int(time.time())}"
    print(f"[session] {session}")

    # ── C4 序列：A 建会话并写入历史 → B 直接收养 ────────────────
    a1 = chat(args.base, tok_a, session, "我的收货地址是上海市徐汇区龙华路3456号，请记住",
              direct=args.direct, actor=actor_a)
    print(f"[A1] {a1[:80].replace(chr(10), ' ')}")
    b1 = chat(args.base, tok_b, session, "我的收货地址是什么？",
              direct=args.direct, actor=actor_b)
    print(f"[B1(收养后问地址)] {b1[:160].replace(chr(10), ' ')}")
    adopted = ("龙华路" in b1 or "徐汇" in b1 or "上海" in b1)
    print(f"[C4] B 收养 A 会话后{'读到了' if adopted else '未读到'} A 的私人历史 → "
          f"{'**LEAK（不安全）**' if adopted else 'isolated'}")

    # ── C3 序列：A/B 都走 travel，同 thread 碰撞 ─────────────────
    chat(args.base, tok_a, session, "帮我规划厦门的行程", direct=args.direct, actor=actor_a)
    chat(args.base, tok_a, session, "三天", direct=args.direct, actor=actor_a)
    chat(args.base, tok_b, session, "帮我规划杭州的行程", direct=args.direct, actor=actor_b)
    chat(args.base, tok_b, session, "两天", direct=args.direct, actor=actor_b)
    a2 = chat(args.base, tok_a, session, "改成五天", direct=args.direct, actor=actor_a)
    print(f"[A2(改成五天)] {a2[:200].replace(chr(10), ' ')}")
    contaminated = "杭州" in a2
    print(f"[C3] A 的行程续跑出现{'杭州（B 的目的地）' if contaminated else '厦门（无污染）'} → "
          f"{'**CHECKPOINT 跨租户污染**' if contaminated else 'isolated'}")

    # ── 落库物证 ────────────────────────────────────────────────
    rows = db(f"SELECT user_id, count(*) FROM chat_sessions "
              f"WHERE session_id='{session}' GROUP BY user_id")
    msgs = db(f"SELECT count(*) FROM chat_messages WHERE session_id='{session}'")
    ck = db(f"SELECT thread_id FROM checkpoints WHERE thread_id LIKE 'travel:{session}' "
            f"OR thread_id LIKE 'travel:%{session}%' LIMIT 3")
    print(f"[db] chat_sessions owners: {rows.splitlines()}")
    print(f"[db] chat_messages(共享 session)={msgs}")
    print(f"[db] travel checkpoint threads: {ck.splitlines() or '无'}")

    verdict = not (adopted or contaminated)
    print(f"\n[result] {'TENANT_COLLISION_ISOLATED' if verdict else 'TENANT_COLLISION_LEAK_CONFIRMED'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
