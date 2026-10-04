#!/usr/bin/env python
"""scripts/travel_a2_decision_probe.py — A2 决策留痕核对探针（验收 #10）

用法：python scripts/travel_a2_decision_probe.py <conversation_id>
经 APISIX 9080 自签 JWT 查 GET /api/travel/decisions，断言最近决策含
decision=canvas_replace 且 payload.entry=candidates_panel（分类候选表换入）。
密钥一律 docker exec printenv 现取（仓库 .env 已漂移）；会话键写 auth-redis
（带密码），jti 用随机值。
"""
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def _exec(container: str, expr: str) -> str:
    out = subprocess.run(
        ["docker", "exec", container, "printenv", expr],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: travel_a2_decision_probe.py <conversation_id>")
        return 2
    cid = sys.argv[1]
    jwt_secret = _exec("agent-app-1", "JWT_SECRET")
    api_key = _exec("agent-app-1", "API_KEY")
    auth_url = _exec("agent-app-1", "AUTH_REDIS_URL")
    # AUTH_REDIS_URL 形如 redis://:<pw>@auth-redis:6379/0
    auth_pw = auth_url.split("redis://:", 1)[-1].split("@", 1)[0]
    jti = uuid.uuid4().hex
    subprocess.run(
        ["docker", "exec", "agent-auth-redis", "redis-cli", "-a", auth_pw,
         "--no-auth-warning", "SET", f"auth:session:440:{jti}", "1", "EX", "1800"],
        capture_output=True, text=True, check=True)

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS512", "typ": "JWT"}).encode())
    payload = b64(json.dumps({
        "iss": "agent-platform", "sub": "440", "userId": 440,
        "username": "uitest_user", "roles": ["user"], "tenant_id": "default",
        "type": "access", "iat": now, "exp": now + 1800, "jti": jti,
    }).encode())
    sig = b64(hmac.new(jwt_secret.encode(), header + b"." + payload,
                       hashlib.sha512).digest())
    token = (header + b"." + payload + b"." + sig).decode()

    req = urllib.request.Request(
        f"http://127.0.0.1:9080/api/travel/decisions?conversation_id={cid}",
        headers={"Authorization": f"Bearer {token}", "X-API-Key": api_key,
                 "X-User-Id": "440", "X-User-Name": "uitest_user",
                 "X-User-Roles": "user", "X-Tenant-Id": "default",
                 "X-Auth-Type": "jwt"})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"FAIL decisions HTTP {e.code}: {e.read()[:200]}")
        return 1

    items = body.get("decisions") or body.get("data", {}).get("decisions") or []
    print(f"decisions total={len(items)}")
    hit = None
    for d in items:
        payload_obj = d.get("payload") or {}
        if (d.get("decision") == "canvas_replace"
                and payload_obj.get("entry") == "candidates_panel"):
            hit = d
            break
    if hit is None:
        print("FAIL 未找到 entry=candidates_panel 的 canvas_replace 决策")
        print("recent:", json.dumps(items[:3], ensure_ascii=False)[:500])
        return 1
    print("PASS canvas_replace(entry=candidates_panel) 已落库")
    print("detail:", json.dumps({
        "decision": hit.get("decision"),
        "plan_version": hit.get("plan_version"),
        "source": hit.get("source"),
        "payload": hit.get("payload"),
        "created_at": hit.get("created_at"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
