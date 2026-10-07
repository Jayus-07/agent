# -*- coding: utf-8 -*-
"""obsrecon_faultinject.py — Grafana 观测重构 Phase 6 故障注入（真实执行，可逆）。

场景（验收 D 组）：
  A worker_down    docker compose stop agent-worker → CeleryWorkerDown firing
                   → start 恢复 → resolved（指标：celery_worker_up / ALERTS）
  B tool_timeout   docker pause mcp-12306（内存保留，unpause 完全恢复）
                   → 发 12306 车票查询 → agent_tool_timeout/failure/unavailable 出数
                   → unpause 恢复 → 恢复后请求正常
  C rag_reject     知识库外问题 → Evidence Gate 拒答
                   → rag_query_total{status=rejected} / rag_short_circuit_total 出数

所有流量 session_id 前缀 obsrecon-test；容器操作仅 pause/stop+对偶恢复，
全程记录前后快照，任何一步失败立即执行恢复路径。
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

GATEWAY = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
PROM = os.getenv("OBSRECON_PROM", "http://127.0.0.1:9090")
TAG = "obsrecon-test"


def sh(cmd: str) -> str:
    """执行 docker compose 命令（仓库根）。"""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120,
                       cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    return (r.stdout + r.stderr).strip()


def prom_query(expr: str) -> list:
    url = f"{PROM}/api/v1/query?query=" + urllib.parse.quote(expr)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode()).get("data", {}).get("result", [])
    except Exception:
        return []


def prom_val(expr: str) -> float | None:
    rs = prom_query(expr)
    return float(rs[0]["value"][1]) if rs else None


def firing_alerts() -> list[str]:
    rs = prom_query('ALERTS{alertstate="firing"}')
    return sorted({r["metric"].get("alertname", "?") for r in rs})


# ── 与 obsrecon_e2e 同一套认证 + SSE（复制保持脚本自包含）──
def _request(method, url, body=None, token=None, timeout=180):
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


import base64  # noqa: E402


def chat_sse(token: str, user_id: str, tenant_id: str, name: str,
             question: str, timeout: float = 240) -> dict:
    body = {"question": question, "session_id": f"{TAG}-{name}",
            "request_id": f"{TAG}-{name}-{int(time.time() * 1000)}",
            "idempotency_key": f"{TAG}-{name}-{int(time.time() * 1000)}"}
    req = urllib.request.Request(f"{GATEWAY}/api/chat/stream",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-User-Id", user_id)
    req.add_header("X-Tenant-Id", tenant_id or "default")
    if os.getenv("API_KEY"):
        req.add_header("X-API-Key", os.getenv("API_KEY"))
    events: dict[str, int] = {}
    answer = ""
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return {"status": e.code, "events": {}, "answer": "",
                "error": e.read().decode("utf-8", "replace")[:150]}
    with resp:
        buf = ""
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = None
                for line in block.splitlines():
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                        events[ev] = events.get(ev, 0) + 1
                    elif line.startswith("data:") and ev == "done":
                        try:
                            p = json.loads(line[5:].strip())
                            answer = str(p.get("reply") or p.get("answer") or "")[:100]
                        except Exception:
                            pass
    return {"status": 200, "events": events, "answer": answer,
            "elapsed": round(time.time() - t0, 1)}


def scenario_a_worker_down() -> dict:
    out: dict = {"scenario": "A_worker_down", "steps": []}
    out["steps"].append({"before_worker_up": prom_val("min(celery_worker_up)"),
                         "before_alerts": firing_alerts()})
    out["steps"].append({"stop": sh("docker compose stop agent-worker")[:200]})
    time.sleep(150)  # CeleryWorkerDown for=2m
    out["steps"].append({"during_worker_up": prom_val("min(celery_worker_up) or on() vector(-1)"),
                         "during_alerts": firing_alerts()})
    out["worker_down_alert_fired"] = "CeleryWorkerDown" in out["steps"][-1]["during_alerts"]
    out["steps"].append({"start": sh("docker compose start agent-worker")[:200]})
    time.sleep(90)
    out["steps"].append({"after_worker_up": prom_val("min(celery_worker_up)"),
                         "after_alerts": firing_alerts()})
    out["recovered"] = (out["steps"][-1]["after_worker_up"] == 1.0
                        and "CeleryWorkerDown" not in out["steps"][-1]["after_alerts"])
    return out


def scenario_b_tool_timeout(token, user_id, tenant_id) -> dict:
    out: dict = {"scenario": "B_tool_timeout_mcp12306", "steps": []}
    metric = 'sum(agent_tool_timeout_total{domain=~"travel|train|web_search"}) or on() vector(0)'
    fail_metric = 'sum(agent_tool_failure_total{domain=~"travel|train|web_search"}) or on() vector(0)'
    out["steps"].append({"before_timeout": prom_val(metric),
                         "before_failure": prom_val(fail_metric)})
    out["steps"].append({"pause": sh("docker pause mcp-12306")[:200]})
    try:
        r = chat_sse(token, user_id, tenant_id, "fi_tool_timeout",
                     "帮我查一下明天福州到厦门的高铁票")
        out["chat"] = {"status": r["status"], "events": r["events"],
                       "answer_head": r.get("answer", "")[:80]}
        time.sleep(20)  # 等 scrape（15s 间隔）
        out["steps"].append({"during_timeout": prom_val(metric),
                             "during_failure": prom_val(fail_metric)})
        out["timeout_increased"] = (out["steps"][-1]["during_timeout"] or 0) > (out["steps"][0]["before_timeout"] or 0)
        out["failure_increased"] = (out["steps"][-1]["during_failure"] or 0) > (out["steps"][0]["before_failure"] or 0)
    finally:
        out["steps"].append({"unpause": sh("docker unpause mcp-12306")[:200]})
    time.sleep(5)
    out["steps"].append({"mcp12306_status": sh("docker inspect mcp-12306 --format '{{.State.Status}}'")})
    return out


def scenario_c_rag_reject(token, user_id, tenant_id) -> dict:
    out: dict = {"scenario": "C_rag_reject", "steps": []}
    rej = 'sum(increase(rag_query_total{status="rejected"}[15m])) or on() vector(0)'
    sc = 'sum(increase(rag_short_circuit_total[15m])) or on() vector(0)'
    out["steps"].append({"before_rejected": prom_val(rej), "before_short_circuit": prom_val(sc)})
    r = chat_sse(token, user_id, tenant_id, "fi_rag_reject",
                 "量子纠缠技术目前在商业快递分拣机器人中的具体应用参数是什么？")
    out["chat"] = {"status": r["status"], "events": r["events"],
                   "answer_head": r.get("answer", "")[:80]}
    time.sleep(20)
    out["steps"].append({"after_rejected": prom_val(rej), "after_short_circuit": prom_val(sc)})
    out["rejected_increased"] = (out["steps"][-1]["after_rejected"] or 0) > (out["steps"][0]["before_rejected"] or 0)
    out["short_circuit_increased"] = (out["steps"][-1]["after_short_circuit"] or 0) > (out["steps"][0]["before_short_circuit"] or 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--username", default="local_super_admin")
    ap.add_argument("--scenario", default="all",
                    help="all | A | B | C")
    args = ap.parse_args()
    results = []
    token, user_id, tenant_id = login(args.username, args.password)
    print(f"login OK user={user_id}")

    if args.scenario in ("all", "C"):
        print("\n=== C: RAG 拒答（零破坏）===")
        r = scenario_c_rag_reject(token, user_id, tenant_id)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:1200])
    if args.scenario in ("all", "B"):
        print("\n=== B: Tool 超时（pause mcp-12306，可逆）===")
        r = scenario_b_tool_timeout(token, user_id, tenant_id)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:1500])
    if args.scenario in ("all", "A"):
        print("\n=== A: Worker 停止/恢复（约 4 分钟）===")
        r = scenario_a_worker_down()
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:1500])

    json.dump(results, open(f"d:/tmp/{TAG}_faultinject.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nsaved d:/tmp/{TAG}_faultinject.json")


if __name__ == "__main__":
    main()
