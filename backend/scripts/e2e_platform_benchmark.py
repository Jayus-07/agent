"""e2e_platform_benchmark.py — Platform Readiness STOP F 性能/SLO 基线。

经真实网关对六个核心域采样（默认各 4 次），记录：
  total latency（SSE 全程）/ TTFT（首 delta）/ output tokens / cost（done.usage）
并核查：prometheus 抓取目标 up、app 指标族在册、multiproc ghost（重复 TYPE 行）。
输出 Initial Production Baseline（BASELINE 口径，非 SLO 承诺）。
"""
from __future__ import annotations

import json
import os
import statistics
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
SAMPLES = int(os.getenv("BENCH_SAMPLES", "4"))


def _request(method, url, body=None, token=None, timeout=300):
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


def login(username: str, password: str) -> str:
    st, body = _request("POST", f"{GATEWAY}/api/auth/login",
                        body={"username": username, "password": password})
    assert st == 200, body[:150]
    return (json.loads(body).get("data") or {}).get("token")


def bench_turn(token: str, session: str, question: str) -> dict:
    body = {"question": question, "session_id": session,
            "request_id": f"bench-{session}-{int(time.time()*1000)}",
            "idempotency_key": f"bench-{session}-{int(time.time()*1000)}"}
    req = urllib.request.Request(f"{GATEWAY}/api/chat/stream",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    if os.getenv("API_KEY"):
        req.add_header("X-API-Key", os.getenv("API_KEY"))
    t0 = time.time()
    ttft = total_usage = None
    out_tokens = 0.0
    answer_len = 0
    with urllib.request.urlopen(req, timeout=300) as resp:
        buffer, ev = "", None
        while True:
            chunk = resp.read(512)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", "replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line.split(":", 1)[1].strip())
                    except ValueError:
                        continue
                    if ev == "delta" and ttft is None:
                        ttft = time.time() - t0
                    if ev == "delta" and isinstance(d, dict):
                        answer_len += len(d.get("content") or "")
                    if ev == "done" and isinstance(d, dict):
                        total_usage = d.get("usage")
    return {"total": time.time() - t0, "ttft": ttft,
            "usage": total_usage or {}, "answer_len": answer_len}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


CASES = [
    ("general", "用一句话介绍库存预警的作用"),
    ("rag", "知识库里客户生命周期分为哪些阶段"),
    ("sql", "查一下最近一个月每天的销售额"),
    ("cs", "帮我查一下这个订单的物流进度，到底什么时候到"),
    ("travel", "帮我规划厦门的行程"),
    ("selection", "给宠物零食做一次智能选品"),
]


def main() -> int:
    token = login("e2e_domain", "DRE2e-2026-Tmp")
    print("[login] OK")
    report: dict[str, dict] = {}
    for name, question in CASES:
        session = f"bench-{name}-{int(time.time())}"
        totals, ttfts, out_toks = [], [], []
        ok = 0
        for i in range(SAMPLES):
            try:
                r = bench_turn(token, session, question)
                totals.append(r["total"])
                if r["ttft"]:
                    ttfts.append(r["ttft"])
                u = r["usage"]
                if isinstance(u, dict):
                    for m in (u.get("models") or u.get("by_model") or {}).values():
                        out_toks.append(float((m or {}).get("output_tokens") or 0))
                ok += 1
                print(f"  [{name}#{i+1}] total={r['total']:.1f}s ttft="
                      f"{(r['ttft'] or 0):.1f}s usage_keys={list((r['usage'] or {}).keys())[:3]}")
            except Exception as exc:
                print(f"  [{name}#{i+1}] ERROR {str(exc)[:90]}")
        report[name] = {
            "ok": ok, "samples": SAMPLES,
            "p50_total": round(pct(totals, 50), 1),
            "p95_total": round(pct(totals, 95), 1),
            "p50_ttft": round(pct(ttfts, 50), 1),
            "out_tokens_sum": round(sum(out_toks), 0),
        }
        print(f"  → {name}: ok={ok}/{SAMPLES} p50={report[name]['p50_total']}s "
              f"p95={report[name]['p95_total']}s ttft_p50={report[name]['p50_ttft']}s\n")

    # ── 任务侧基线（DB 近 20 个 SUCCESS 任务）────────────────────
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c",
         "SELECT coalesce(round(avg(extract(epoch from (finished_at-created_at)))::numeric,1),0) "
         "FROM tasks WHERE status='SUCCESS' AND finished_at IS NOT NULL AND "
         "created_at > now() - interval '2 days'"],
        capture_output=True, text=True)
    task_avg = out.stdout.strip()
    print(f"[task] 近2日 SUCCESS 任务平均时长: {task_avg}s")

    # ── F10 Prometheus 抓取面 + multiproc ghost ─────────────────
    st, targets = _request("GET", "http://127.0.0.1:9090/api/v1/targets?state=active",
                           timeout=15)
    up = 0
    if st == 200:
        data = json.loads(targets).get("data", {}).get("activeTargets", [])
        ups = [t.get("labels", {}).get("job") for t in data if t.get("health") == "up"]
        up = len(ups)
        print(f"[prom] active targets up: {up} jobs={sorted(set(ups))[:10]}")
    st, metrics = _request("GET", "http://127.0.0.1:8000/metrics", timeout=20)
    if st == 200:
        families = [l.split(" ")[0] for l in metrics.splitlines()
                    if l.startswith("# TYPE ")]
        ghost = len(families) - len(set(families))
        key_families = [f for f in ("routing_domain_total", "chat_request_total",
                                    "task_terminal_total", "llm_requests_total",
                                    "context_compactions_total")
                        if any(f in x for x in families)]
        print(f"[metrics] families={len(families)} ghost_dup={ghost} "
              f"key_families={len(key_families)}/5")

    print("\n===== Initial Production Baseline（BASELINE，非 SLO 承诺）=====")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({"task_avg_seconds": task_avg,
                      "prom_targets_up": up,
                      "metrics_ghost_dup": ghost if st == 200 else "n/a"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
