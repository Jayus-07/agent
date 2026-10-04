# -*- coding: utf-8 -*-
"""scripts/travel_force_kill_release_verify.py — 强杀释放验证（验收 #135）

执行中 SIGKILL app 容器 → 重启 → 等用户并发槽 RedisLease TTL（30s）到期 →
同会话再发请求必须 200 可执行（锁最终释放，不永久悬空）。

⚠️ 会 SIGKILL 生产 app 容器（约 40s 不可用），只允许在开发/验证环境跑：
  D:/Python/python.exe scripts/travel_force_kill_release_verify.py
"""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9080"
REPO = r"D:/Program Files/workplace/agent"
API_KEY = "ak_P8iOQbZT4kgUcA8ImY-CycGLseGMIOMiB9jUaUxx4qGo"
LEASE_TTL_S = 32  # RedisLease ttl=30 + 余量


def login():
    body = json.dumps({"username": "uitest_user", "password": "UiTest2026"}).encode()
    req = urllib.request.Request(
        BASE + "/api/auth/login", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["token"]


def stream_once(token, sid, question, timeout=100):
    body = json.dumps({"question": question, "session_id": sid,
                       "domain_hint": ""}).encode()
    req = urllib.request.Request(
        BASE + "/api/chat/stream", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY,
                 "Authorization": f"Bearer {token}",
                 "X-User-Id": "uitest_user", "Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    token = login()
    sid = f"kill-{int(time.time())}"

    # ① 占住 user 并发槽的执行中请求，2s 后 SIGKILL
    holder = {"status": None}

    def hold():
        holder["status"] = stream_once(token, sid, "厦门3天2人，预算3000",
                                       timeout=8)

    t = threading.Thread(target=hold)
    t.start()
    time.sleep(2)
    subprocess.run(["docker", "kill", "agent-app-1"], capture_output=True)
    print("① 已 SIGKILL agent-app-1（请求执行中）")
    t.join()

    # ② 重启并等健康
    subprocess.run(["docker", "compose", "up", "-d", "app"], cwd=REPO,
                   capture_output=True)
    for _ in range(30):
        time.sleep(2)
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8000/health", timeout=3) as r:
                if r.status == 200:
                    print("② app 重启 healthy")
                    break
        except Exception:
            continue
    else:
        print("FAIL app 未恢复")
        return 1

    # ③ 等 lease TTL 到期，新请求必须可执行
    print(f"③ 等待 {LEASE_TTL_S}s（RedisLease ttl=30 到期）...")
    time.sleep(LEASE_TTL_S)
    status = stream_once(token, sid, "福州2天2人")
    if status == 200:
        print("PASS 强杀后锁最终释放，新请求 200 可执行")
        return 0
    print(f"FAIL 强杀后新请求被拒: HTTP {status}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
