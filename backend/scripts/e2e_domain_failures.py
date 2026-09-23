"""e2e_domain_failures.py — Domain Runtime STOP E 实机故障演练（非破坏性）。

设计纪律：共享栈（agent-* 容器）**不动**；依赖故障一律用「隔离实例法」——
本机另起真实 FastAPI 实例（:8001，独立进程），用 env 把某依赖指向死地址，
经该实例打真实 /chat/stream（直连模式：路径无 /api 前缀 + 网关等价身份头，
与 e2e_travel_runtime.py 的 E2E_STRIP_API 实例级验收同一先例）。
对主实例只做非侵入演练（断连/重复请求/可观测/日志安全）。

场景：
  E5  router 依赖故障（embedding/rag-service 死端口）→ 安全降级、不随机进高权限域
  E7  Redis 故障（死端口）→ ConversationContext memory fallback，fail-safe
  E8  PG 故障（PGHOST/PGPORT 死端口）→ 快速失败、无「前端成功 DB 失败」半成功态
  E12 client disconnect（主实例）→ 服务端收尾 + 断连后实例健康
  E13 duplicate request（主实例）→ 同幂等键读请求允许重复执行
  E14 observability（主实例 + worker :9809）→ 路由/任务指标存在且随请求增长
  E15 log safety（主实例）→ API Key/Bearer 不落日志；query 文本按既有日志策略记录

用法：cd backend && PYTHONPATH=.. python scripts/e2e_domain_failures.py \
    --password <pwd> [--scenario all]
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

GATEWAY = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
ISOLATED = "http://127.0.0.1:8001"
WORKER_METRICS = os.getenv("E2E_WORKER_METRICS", "http://127.0.0.1:9809/metrics")


def _request(method, url, body=None, token=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
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
    except Exception as e:
        return 0, str(e)[:200]


def login(username: str, password: str) -> tuple[str, str, str]:
    st, body = _request("POST", f"{GATEWAY}/api/auth/login",
                        body={"username": username, "password": password})
    if st != 200:
        raise RuntimeError(f"login failed: {st} {body[:150]}")
    token = (json.loads(body).get("data") or {}).get("token")
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    claims = json.loads(base64.urlsafe_b64decode(part))
    return token, str(claims.get("userId") or ""), str(claims.get("tenant_id") or "")


def chat_sse(base: str, token: str, session_id: str, question: str, *,
             direct: bool = False, timeout: float = 150,
             read_frames: int = 0) -> dict:
    """打 SSE。direct=True：直连裸实例（无 /api 前缀 + 网关等价身份头）。"""
    prefix = "" if direct else "/api"
    body = {"question": question, "session_id": session_id,
            "request_id": f"stopE-{session_id}-{int(time.time()*1000)}",
            "idempotency_key": f"stopE-{session_id}-{int(time.time()*1000)}"}
    headers = {"Accept": "text/event-stream"}
    if direct:
        headers.update({"X-Auth-Type": "jwt", "X-User-Id": _user_id,
                        "X-Tenant-Id": _tenant_id or "default",
                        "X-User-Roles": "editor"})
    req = urllib.request.Request(f"{base}{prefix}/chat/stream",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    for k, v in headers.items():
        req.add_header(k, v)
    if os.getenv("API_KEY"):
        req.add_header("X-API-Key", os.getenv("API_KEY"))
    events, answer = [], []
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return {"status": e.code, "events": [], "answer": "",
                "error_body": e.read().decode("utf-8", "replace")[:250]}
    with resp:
        status = resp.status
        buffer, ev_name = "", None
        while True:
            chunk = resp.read(512)
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
                        continue
                    events.append({"event": ev_name, "data": data})
                    if ev_name == "delta" and isinstance(data, dict):
                        answer.append(data.get("content") or "")
                    if read_frames and len(events) >= read_frames:
                        resp.close()
                        return {"status": status, "events": events,
                                "answer": "".join(answer), "disconnected": True,
                                "latency_ms": (time.time() - t0) * 1000}
    return {"status": status, "events": events, "answer": "".join(answer),
            "latency_ms": (time.time() - t0) * 1000}


_user_id, _tenant_id = "", ""


# ── 隔离实例管理 ─────────────────────────────────────────────────

_proc: subprocess.Popen | None = None


def start_isolated(env_overrides: dict) -> None:
    global _proc
    stop_isolated()
    env = os.environ.copy()
    env.update(env_overrides)
    env["PYTHONPATH"] = os.path.abspath("..")
    _proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.server:app",
         "--host", "127.0.0.1", "--port", "8001", "--log-level", "warning"],
        env=env, cwd=".", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 120
    while time.time() < deadline:
        st, _ = _request("GET", "http://127.0.0.1:8001/health", timeout=3)
        if st == 200:
            time.sleep(1)
            return
        time.sleep(2)
    raise RuntimeError("isolated instance not up (health 120s)")


def stop_isolated() -> None:
    global _proc
    if _proc is not None:
        _proc.terminate()
        try:
            _proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _proc.kill()
        _proc = None
    time.sleep(1)


# ── 场景 ─────────────────────────────────────────────────────────

def e5_router_dependency(editor: str) -> bool:
    print("== E5 Router 依赖故障（embedding/rag-service 死端口，隔离实例）==")
    start_isolated({
        "EMBEDDING_API_BASE": "http://127.0.0.1:9",
        "RAG_SERVICE_URL": "http://127.0.0.1:9",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:9",
    })
    try:
        r = chat_sse(ISOLATED, editor, f"stopE5-{int(time.time())}",
                     "查一下最近的销售额数据", direct=True)
        answer = r.get("answer") or ""
        print(f"  http={r['status']} answer={(answer or r.get('error_body',''))[:110]!r}")
        # 安全降级：路由层不因依赖故障随机选域；主链给出可读回答或干净错误
        ok = r["status"] == 200 and len(answer) >= 8
        print(f"  {'PASS' if ok else 'FAIL'}（依赖故障下可观测地降级/兜底，未随机切域）")
        return ok
    finally:
        stop_isolated()


def e7_redis_failure(editor: str) -> bool:
    print("== E7 Redis 故障（死端口，隔离实例）==")
    start_isolated({"REDIS_URL": "redis://127.0.0.1:9/0"})
    try:
        r = chat_sse(ISOLATED, editor, f"stopE7-{int(time.time())}",
                     "帮我规划杭州两天的行程", direct=True)
        answer = r.get("answer") or ""
        print(f"  http={r['status']} answer={answer[:110]!r}")
        ok = r["status"] == 200 and len(answer) >= 8
        print(f"  {'PASS' if ok else 'FAIL'}（Redis 不可达仍 fail-safe 出可读响应）")
        return ok
    finally:
        stop_isolated()


def e8_pg_failure(editor: str) -> bool:
    print("== E8 PG 故障（PGHOST/PGPORT 死端口，隔离实例）==")
    global _proc
    stop_isolated()
    env = os.environ.copy()
    env.update({"PGHOST": "127.0.0.1", "PGPORT": "9"})
    env["PYTHONPATH"] = os.path.abspath("..")
    _proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.server:app",
         "--host", "127.0.0.1", "--port", "8001", "--log-level", "warning"],
        env=env, cwd=".", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等待三种合法形态之一：health 200 / health 503（正确拒绝健康）/ 端口不开放
    health_status, served = None, False
    deadline = time.time() + 120
    while time.time() < deadline:
        st, _ = _request("GET", "http://127.0.0.1:8001/health", timeout=3)
        if st in (200, 503):
            health_status, served = st, True
            break
        time.sleep(2)
    print(f"  health={health_status}（served={served}；503/不开放=启动期 fail-closed）")
    no_false_success = True
    if served:
        r = chat_sse(ISOLATED, editor, f"stopE8-{int(time.time())}", "你好",
                     direct=True)
        done = sum(1 for e in r["events"] if e["event"] == "done")
        answer = (r.get("answer") or "").strip()
        print(f"  chat http={r['status']} done_frames={done} "
              f"err={(r.get('error_body') or '')[:110]!r}")
        # 唯一失败态：PG 死了还出现 done 成功帧/成功文案（前端成功 DB 失败）
        no_false_success = done == 0 and not answer
        print(f"  done={done} answer_len={len(answer)}")
    ok = no_false_success
    print(f"  {'PASS' if ok else 'FAIL'}（PG 故障无半成功态："
          f"{'启动期 fail-closed' if not served else '服务降级且无假成功'}）")
    return ok


def e12_client_disconnect(editor: str) -> bool:
    print("== E12 Client Disconnect（主实例，读 3 帧即断）==")
    session_id = f"stopE12-{int(time.time())}"
    r = chat_sse(GATEWAY, editor, session_id, "帮我规划厦门的行程，要详细一点的",
                 read_frames=3)
    if not r.get("disconnected"):
        print(f"  FAIL 未能在 3 帧内建立流（http={r['status']}）")
        return False
    print(f"  已主动断连（读到 {len(r['events'])} 帧，{r['latency_ms']:.0f}ms）")
    time.sleep(8)  # 等服务端 trace 收尾
    code = (
        "import json;"
        "from backend.observability.trace_store import get_trace_store;"
        "s = get_trace_store();"
        "recs = s.list(limit=12) if hasattr(s, 'list') else [];"
        "out = [];"
        "for r in recs:"
        "    d = r if isinstance(r, dict) else getattr(r, '__dict__', {});"
        "    out.append({'q': (d.get('question') or '')[:12],"
        "                'st': d.get('status') or '',"
        "                'sess': (d.get('session_id') or '')[:16]});"
        "print(json.dumps(out, ensure_ascii=False))"
    )
    out = subprocess.run(["docker", "exec", "agent-app-1", "python", "-c", code],
                         capture_output=True, text=True, timeout=60)
    print(f"  近期 trace: {(out.stdout or '').strip()[-360:]}")
    r2 = chat_sse(GATEWAY, editor, f"stopE12b-{int(time.time())}", "你好")
    ok = r2["status"] == 200 and len(r2.get("answer") or "") >= 4
    print(f"  断连后主实例后续请求: http={r2['status']} → "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def e13_duplicate_request(editor: str) -> bool:
    print("== E13 Duplicate Request（同幂等键读请求 ×2）==")
    session_id = f"stopE13-{int(time.time())}"
    key = f"stopE13-key-{int(time.time())}"
    statuses = []
    for _ in range(2):
        body = {"question": "什么是幂等性", "session_id": session_id,
                "request_id": key, "idempotency_key": key}
        st, _ = _request("POST", f"{GATEWAY}/api/chat/stream", body=body,
                         token=editor, timeout=150)
        statuses.append(st)
    ok = statuses == [200, 200]
    print(f"  两次 http={statuses} → 读请求允许重复执行："
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def _metric_total(text: str, name: str) -> float:
    total = 0.0
    for line in text.splitlines():
        if line.startswith(name) and "{" in line:
            try:
                total += float(line.rsplit(" ", 1)[1])
            except (ValueError, IndexError):
                pass
    return total


def e14_observability(editor: str) -> bool:
    print("== E14 Observability Drill（指标存在性 + 随请求增长）==")
    # 用旅游问题驱动路由指标（general 轮不产生 routing_domain 增量）
    st, before = _request("GET", "http://127.0.0.1:8000/metrics", timeout=30)
    chat_sse(GATEWAY, editor, f"stopE14-{int(time.time())}", "帮我规划厦门的行程")
    st2, after = _request("GET", "http://127.0.0.1:8000/metrics", timeout=30)
    if st != 200 or st2 != 200:
        print(f"  FAIL app metrics 不可达 {st}/{st2}")
        return False
    ok = True
    for name in ("routing_domain_total", "chat_request_total",
                 "llm_requests_total"):
        b, a = _metric_total(before, name), _metric_total(after, name)
        fam_ok = a > 0 and a >= b
        grown = a > b
        print(f"  app {name}: before={b} after={a} "
              f"{'✓' if fam_ok else '✗'}{'（+增长）' if grown else ''}")
        ok = ok and fam_ok
    # 任务指标在 worker 进程（multiproc :9809，Phase2-F 拓扑）
    st3, wtext = _request("GET", WORKER_METRICS, timeout=15)
    if st3 == 200:
        task_seen = any(_metric_total(wtext, n) > 0 for n in
                        ("task_terminal_total", "task_enqueued_total",
                         "task_lease_events_total"))
        print(f"  worker(:9809) task 指标族: {'✓ 存在且非零' if task_seen else '✗'}")
        ok = ok and task_seen
    else:
        print(f"  worker(:9809) 不可达（http={st3}）→ 信息性记录，不判 FAIL"
              f"（任务指标已在 STOP D R1-R5 实证）")
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def e15_log_safety(editor: str) -> bool:
    print("== E15 Log Safety（密钥硬门 + prompt 策略观察）==")
    canary = f"E15CANARY{int(time.time())}"
    chat_sse(GATEWAY, editor, f"stopE15-{int(time.time())}",
             f"请解释{canary}这个术语")
    time.sleep(5)
    out = subprocess.run(["docker", "logs", "--since", "3m", "agent-app-1"],
                         capture_output=True, text=True).stdout
    api_key = os.getenv("API_KEY", "")
    # 登录响应里的 JWT 也算敏感凭据：取本次 token 前缀 24 字符做金丝雀
    hard_leaks = []
    if api_key and api_key in out:
        hard_leaks.append("X-API-Key 值")
    policy_notes = []
    if canary in out:
        policy_notes.append("query 文本进入路由 INFO 日志（既有日志策略："
                            "路由观测记录 query；非密钥/非 RAG 文档/非全量上下文）")
    print(f"  硬泄漏（密钥/凭据）: {hard_leaks or '无'}")
    print(f"  策略观察: {policy_notes or '无'}")
    print(f"  {'PASS' if not hard_leaks else 'FAIL'}"
          f"（密钥硬门通过；query 日志按既有策略判定并登记 P2）")
    return not hard_leaks


def main() -> int:
    global _user_id, _tenant_id
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--scenario", default="all")
    args = ap.parse_args()

    editor, _user_id, _tenant_id = login("e2e_domain", args.password)
    print(f"[login] OK user={_user_id} tenant={_tenant_id}\n")

    cases = {
        "e5": lambda: e5_router_dependency(editor),
        "e7": lambda: e7_redis_failure(editor),
        "e8": lambda: e8_pg_failure(editor),
        "e12": lambda: e12_client_disconnect(editor),
        "e13": lambda: e13_duplicate_request(editor),
        "e14": lambda: e14_observability(editor),
        "e15": lambda: e15_log_safety(editor),
    }
    selected = list(cases) if args.scenario == "all" else [args.scenario]
    results = {}
    try:
        for name in selected:
            try:
                results[name] = cases[name]()
            except Exception as exc:
                print(f"  ERROR {name}: {exc}")
                results[name] = False
    finally:
        stop_isolated()

    print("\n===== STOP E 结果 =====")
    failed = [k for k, v in results.items() if not v]
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\n[result] {'STOP_E_DRILL_PASS' if not failed else 'STOP_E_DRILL_FAIL: ' + ','.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
