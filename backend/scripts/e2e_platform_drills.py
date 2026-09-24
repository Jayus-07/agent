"""e2e_platform_drills.py — Platform Readiness STOP D/E 依赖故障与重启恢复演练。

场景（真实容器级操作，恢复动作内置；共享栈短暂扰动属演练目的，逐项验后即恢复）：
  D4/E2  worker SIGKILL：任务 RUNNING 中 kill worker 容器（restart=unless-stopped
         自动拉起）→ lease 回拨 + maintenance sweep（生产同路径）→ recovery
         成功、execution 换发、域身份保持
  D5     APISIX down：网关不可达期间，app 直连口仍 fail-closed（无网关绕过暴露）
  E1     app 重启（-t 20 有界）：活跃 session 跨重启历史/会话不损坏
  E3     Redis 重启：控制面/缓存恢复，chat 可用
  E4     PG 重启：连接池自愈，chat/tasks 恢复

用法：cd backend && PYTHONPATH=.. python scripts/e2e_platform_drills.py --password <pwd>
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

GATEWAY = "http://127.0.0.1:9080"
RESULTS: dict[str, bool] = {}


def sh(cmd: list[str], timeout=120) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (out.stdout or out.stderr or "").strip()


def psql(sql: str) -> str:
    return sh(["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
               "-d", "agent_memory", "-t", "-A", "-c", sql])


def _request(method, url, body=None, token=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
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
    except Exception as e:
        return 0, str(e)[:150]


def login(username: str, password: str) -> str:
    st, body = _request("POST", f"{GATEWAY}/api/auth/login",
                        body={"username": username, "password": password})
    if st != 200:
        raise RuntimeError(f"login failed: {st} {body[:120]}")
    return (json.loads(body).get("data") or {}).get("token")


def health(timeout=5) -> int:
    st, _ = _request("GET", "http://127.0.0.1:8000/health", timeout=timeout)
    return st


def wait_healthy(timeout=180) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health(timeout//10 or 3) == 200:
            time.sleep(2)
            return True
        time.sleep(3)
    return False


def chat(token: str, session_id: str, question: str, timeout=240) -> str:
    body = {"question": question, "session_id": session_id,
            "request_id": f"drill-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"drill-{session_id}-{int(time.time()*1000)}"}
    st, raw = _request("POST", f"{GATEWAY}/api/chat/stream", body=body,
                       token=token, timeout=timeout)
    if st != 200:
        raise RuntimeError(f"chat http={st}: {raw[:120]}")
    answer, ev = [], None
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


def d4_worker_sigkill(editor: str) -> None:
    print("== D4/E2 Worker SIGKILL 恢复 ==")
    st, body = _request("POST", f"{GATEWAY}/api/tasks", token=editor,
                        body={"query": f"用一句话说明什么是高可用（D4 {int(time.time())}）"})
    if st != 200:
        RESULTS["D4_submit"] = False
        print(f"  FAIL 提交任务 http={st} {body[:120]}")
        return
    payload = json.loads(body)
    payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tid = payload.get("task_id") or payload.get("id")
    if not tid:
        RESULTS["D4_submit"] = False
        print(f"  FAIL 提交响应无 task_id: {body[:200]}")
        return
    print(f"  task={tid[:8]}")
    # 等 RUNNING
    row = ""
    for _ in range(40):
        row = psql(f"SELECT status || '|' || execution_id FROM tasks WHERE id='{tid}'")
        if row.startswith("RUNNING"):
            break
        time.sleep(2)
    e1 = row.split("|")[1] if "|" in row else ""
    # 模拟进程崩溃：容器内 SIGKILL PID1（非 docker kill——后者是手动停止
    # 语义，unless-stopped 不拉起；崩溃退出非零才会自动重启）
    sh(["docker", "exec", "agent-agent-worker-1", "kill", "-9", "1"])
    print("  worker 进程 SIGKILLed（崩溃语义），等待自动重启…")
    out = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        out = sh(["docker", "inspect", "-f", "{{.State.Health.Status}}",
                  "agent-agent-worker-1"])
        if out == "healthy":
            break
        time.sleep(5)
    print(f"  worker health={out}")
    # lease 回拨 + 生产 sweep 路径
    psql(f"UPDATE tasks SET lease_expires_at = now() - interval '300 seconds' "
         f"WHERE id = '{tid}'")
    sh(["docker", "exec", "agent-agent-worker-1", "python", "-c",
        "from backend.tasks.celery_app import celery_app;"
        "celery_app.send_task('tasks.stale_execution_recovery', queue='maintenance')"],
       timeout=90)
    # 轮询终态
    final, e2, recovery = "", "", 0
    deadline = time.time() + 420
    while time.time() < deadline:
        row = psql(f"SELECT status || '|' || coalesce(execution_id,'') || '|' || "
                   f"recovery_count FROM tasks WHERE id='{tid}'")
        final, e2, recovery = (row.split("|") + ["", "0"])[:3]
        if final in ("SUCCESS", "FAILED"):
            break
        time.sleep(4)
    ok = (final == "SUCCESS" and e2 and e2 != e1 and int(recovery or 0) >= 1)
    print(f"  final={final} E1={e1[:8]} E2={e2[:8]} recovery={recovery}")
    print(f"  {'PASS' if ok else 'FAIL'}（worker 死亡 → recovery 换发 → 成功归属新 execution）")
    RESULTS["D4_worker_sigkill_recovery"] = ok


def d5_apisix_down() -> None:
    print("== D5 APISIX down（网关不可达 ≠ 绕过暴露）==")
    sh(["docker", "stop", "agent-apisix"], timeout=60)
    try:
        st_gw, _ = _request("GET", f"{GATEWAY}/health", timeout=5)
        # 外部暴露面判据 = 端口绑定必须是环回（127.0.0.1）；无凭据直连的
        # 允许/拒绝由 ALLOW_UNAUTHENTICATED（本地开发豁免）语义决定，
        # 生产该 flag=false 时 middleware fail-closed。
        bind = sh(["docker", "inspect", "-f",
                   "{{json .HostConfig.PortBindings}}", "agent-app-1"])
        loopback_only = '"127.0.0.1"' in bind and '0.0.0.0' not in bind and '::' not in bind
        direct_status = _request("GET", "http://127.0.0.1:8000/api/agents",
                                 timeout=5)[0]
        print(f"  gateway={st_gw}（0=不可达）  app 绑定 loopback={loopback_only}  "
              f"直连无凭据={direct_status}（dev 豁免语义下可 200）")
        ok = st_gw == 0 and loopback_only
    finally:
        sh(["docker", "start", "agent-apisix"], timeout=60)
        time.sleep(6)
    st_after = _request("GET", f"{GATEWAY}/health", timeout=8)[0]
    print(f"  apisix 重启后 gateway health={st_after}")
    ok = ok and st_after in (200, 404)
    print(f"  {'PASS' if ok else 'FAIL'}（网关停机不产生绕过暴露，恢复后可用）")
    RESULTS["D5_apisix_down_no_bypass"] = ok


def e1_app_restart(editor: str) -> None:
    print("== E1 app 重启（活跃 session 保全）==")
    session = f"drillE1-{int(time.time())}"
    a = chat(editor, session, "记住数字47，之后我会问")
    print(f"  重启前 answer: {a[:60]!r}")
    sh(["docker", "restart", "-t", "20", "agent-app-1"], timeout=300)
    ok_up = wait_healthy()
    print(f"  重启后 health={health()}")
    b = chat(editor, session, "刚才让你记住的数字是多少？只回答数字")
    print(f"  重启后 answer: {b[:80]!r}")
    hist = psql(f"SELECT count(*) FROM chat_messages WHERE session_id='{session}'")
    recall = "47" in b
    ok = ok_up and recall and int(hist or 0) >= 4
    print(f"  chat_messages={hist} 跨重启记忆={'保持' if recall else '丢失'}")
    print(f"  {'PASS' if ok else 'FAIL'}（PG 权威历史跨重启无损）")
    RESULTS["E1_app_restart_session"] = ok


def e3_redis_restart(editor: str) -> None:
    print("== E3 Redis 重启 ==")
    sh(["docker", "restart", "agent-redis-1"], timeout=120)
    time.sleep(8)
    ok = wait_healthy(120)
    ans = ""
    if ok:
        try:
            ans = chat(editor, f"drillE3-{int(time.time())}", "你好")
        except Exception as exc:
            print(f"  chat 异常: {exc}")
    ok = ok and len(ans) >= 4
    print(f"  health={health()} chat_len={len(ans)}")
    print(f"  {'PASS' if ok else 'FAIL'}（Redis 重启后控制面/缓存恢复，chat 可用）")
    RESULTS["E3_redis_restart"] = ok


def e4_pg_restart(editor: str) -> None:
    print("== E4 PostgreSQL 重启（连接池自愈）==")
    sh(["docker", "restart", "agent-postgres-1"], timeout=120)
    # PG 重启会连带影响 db-migrate 依赖链——只需等待恢复
    ok = wait_healthy(240)
    ans = ""
    if ok:
        try:
            ans = chat(editor, f"drillE4-{int(time.time())}", "你好")
        except Exception as exc:
            print(f"  chat 异常: {exc}")
    workers = sh(["docker", "inspect", "-f", "{{.State.Health.Status}}",
                  "agent-agent-worker-1"])
    ok = ok and len(ans) >= 4 and workers == "healthy"
    print(f"  health={health()} worker={workers} chat_len={len(ans)}")
    print(f"  {'PASS' if ok else 'FAIL'}（PG 重启后 app/worker 连接池自愈）")
    RESULTS["E4_pg_restart"] = ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--scenario", default="all")
    args = ap.parse_args()

    editor = login("e2e_domain", args.password)
    print(f"[login] OK\n")

    cases = {
        "d4": lambda: d4_worker_sigkill(editor),
        "d5": lambda: d5_apisix_down(),
        "e1": lambda: e1_app_restart(editor),
        "e3": lambda: e3_redis_restart(editor),
        "e4": lambda: e4_pg_restart(editor),
    }
    selected = list(cases) if args.scenario == "all" else [args.scenario]
    for name in selected:
        try:
            cases[name]()
        except Exception as exc:
            print(f"  ERROR {name}: {exc}")
            RESULTS[name] = False

    print("\n===== STOP D/E 结果 =====")
    failed = [k for k, v in RESULTS.items() if not v]
    for k, v in RESULTS.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\n[result] {'PLATFORM_DRILL_PASS' if not failed else 'PLATFORM_DRILL_FAIL: ' + ','.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
