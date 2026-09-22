# -*- coding: utf-8 -*-
"""d6_routing_regression.py — D6 路由能力漂移实机回归（×10）

验收项（2026-09-22 P0）：「统计本月订单金额」连续实机执行至少 10 次，
全部进入 sql.query（直连 :8000 hierarchical 路由，SSE 全链路 + trace 证据）。

用法（项目根目录）:
    python scripts/d6_routing_regression.py            # 10 轮
    python scripts/d6_routing_regression.py --runs 3   # 冒烟

判定：每轮从 trace 提取 路由决策/工具选择 证据，fine_top1 或 selected_tool
== sql.query 即 PASS；任何一轮出现 data.collect 或澄清/兜底即 FAIL。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
# 服务级 API Key 只从环境读取（2026-09-23 审查 P0-1：此前硬编码已进 git
# 历史，轮换由外部执行）；缺失即失败，错误信息不得回显密钥。
API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    sys.exit("API_KEY environment variable is required")
HDRS = {"X-API-Key": API_KEY}
QUERY = "统计本月订单金额"
EXPECTED = "sql.query"


def post_stream(question: str, session_id: str, timeout: int = 150) -> tuple[str, str]:
    """发 SSE 对话，返回 (终止事件名, trace_id)。"""
    body = json.dumps({"question": question, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/chat/stream", data=body, method="POST",
        headers={"Content-Type": "application/json", "department": "ops", **HDRS},
    )
    done, trace_id = "?", ""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        current = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                current = line[6:].strip()
            elif line.startswith("data:") and current in ("done", "error"):
                try:
                    payload = json.loads(line[5:].strip())
                    trace_id = payload.get("trace_id") or trace_id
                except Exception:
                    pass
                done = current
                return done, trace_id
    return done, trace_id


def _get_json(path: str) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", headers=HDRS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_routing(session_id: str, trace_id: str) -> dict:
    """从 trace 提取路由证据：fine_top1 / route_mode / selected_tool / calibration。"""
    evidence: dict = {}
    trace = None
    for attempt in range(4):
        try:
            if trace_id:
                trace = _get_json(f"/observability/traces/{trace_id}")
            else:
                data = _get_json(f"/observability/traces?session_id={session_id}&limit=1")
                rows = data.get("traces") or []
                if not rows:
                    time.sleep(1.5)
                    continue
                tid = rows[0].get("trace_id") or rows[0].get("id")
                trace = _get_json(f"/observability/traces/{tid}")
            break
        except Exception as e:
            evidence["trace_error"] = str(e)[:120]
            time.sleep(1.5)
    if not trace:
        return evidence

    def walk(sp):
        if isinstance(sp, str) or not isinstance(sp, dict):
            return
        if sp.get("name") == "路由决策":
            for e in sp.get("events") or []:
                msg = str(e.get("message", ""))
                if "fine_top1" in msg or "决定" in msg:
                    evidence.setdefault("router_events", []).append(msg[:160])
            out = sp.get("output") or {}
            meta = out.get("routing_meta") or {}
            if meta:
                evidence["domain"] = meta.get("domain")
                evidence["fine_top1"] = meta.get("fine_top1")
                evidence["route_mode"] = meta.get("tool_route_mode")
                evidence["calibration"] = meta.get("calibration")
        if sp.get("name") == "工具选择":
            evs = sp.get("events") or []
            evidence.setdefault("tool_selector_events", []).extend(
                str(e.get("message", ""))[:120] for e in evs[:6])
        for c in sp.get("children") or []:
            walk(c)

    for sp in trace.get("spans") or []:
        walk(sp)
    md = trace.get("metadata") or {}
    if md.get("tool_selection"):
        evidence["tool_selection"] = md["tool_selection"].get("capability") or \
            md["tool_selection"].get("source")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()

    results = []
    for i in range(1, args.runs + 1):
        session_id = f"d6-regression-{int(time.time())}-{i}"
        t0 = time.time()
        try:
            done, trace_id = post_stream(QUERY, session_id)
        except Exception as e:
            results.append({"run": i, "result": "ERROR", "detail": str(e)[:150]})
            continue
        time.sleep(1)
        ev = extract_routing(session_id, trace_id)
        routed_tool = ev.get("fine_top1") or ev.get("tool_selection") or ""
        # 直通时 selected_tool 即 fine_top1；FC 选中时看 tool_selection
        ok = routed_tool == EXPECTED and done == "done"
        results.append({
            "run": i, "session": session_id, "sse": done,
            "latency_s": round(time.time() - t0, 1),
            "domain": ev.get("domain", ""),
            "routed_tool": routed_tool or "(未取到)",
            "route_mode": ev.get("route_mode", ""),
            "calibration_basis": (ev.get("calibration") or {}).get("basis", "")
            if isinstance(ev.get("calibration"), dict) else "",
            "result": "PASS" if ok else "FAIL",
        })

    print(f"{'run':>3} | {'sse':<6} | {'latency':>8} | {'domain':<8} | "
          f"{'routed_tool':<14} | {'route_mode':<14} | {'calibration':<20} | result")
    print("-" * 110)
    for r in results:
        print(f"{r['run']:>3} | {r.get('sse', ''):<6} | {r.get('latency_s', 0):>7}s | "
              f"{r.get('domain', ''):<8} | {r.get('routed_tool', ''):<14} | "
              f"{r.get('route_mode', ''):<14} | {r.get('calibration_basis', ''):<20} | {r['result']}")

    passed = sum(1 for r in results if r["result"] == "PASS")
    print(f"\nD6 ×{args.runs}: {passed}/{args.runs} 进入 {EXPECTED}")
    return 0 if passed == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
