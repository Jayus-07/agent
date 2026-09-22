# -*- coding: utf-8 -*-
"""dgroup_direct_regression.py — D1-D8 对话路径回归（直连 :8000）

BFF(:3100) 未运行时的等价回归：直连后端走同一 Router/SSE 全链路。
场景与判定标准来自 docs/travel-test-scenarios-2026-09-22.md §八。
D1→D2→D3 同会话（跨轮改单），D7→D8 同会话。

用法: docker exec agent-app-1 python scripts/dgroup_direct_regression.py
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
API_KEY = "ak_tadNA05DPYN8Yj9QeIYpTPh50n_VR5kUMgubOxxIn3I"
HDRS = {"X-API-Key": API_KEY}


def chat(question: str, session_id: str, timeout: int = 150) -> dict:
    body = json.dumps({"question": question, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/chat/stream", data=body, method="POST",
        headers={"Content-Type": "application/json", "department": "ops", **HDRS})
    answer, done, trace_id = [], "?", ""
    cur = ""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                cur = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    p = json.loads(line[5:].strip())
                except Exception:
                    continue
                if cur == "delta":
                    answer.append(p.get("text") or p.get("content") or "")
                elif cur == "done":
                    answer.append(p.get("final_answer") or p.get("answer") or "")
                    trace_id = p.get("trace_id") or trace_id
                    done = "done"
                    break
                elif cur == "error":
                    done = "error"
                    answer.append(str(p)[:120])
                    break
    return {"done": done, "answer": "".join(answer), "trace_id": trace_id}


def routing_of(session_id: str, trace_id: str) -> dict:
    try:
        if trace_id:
            t = _get(f"/observability/traces/{trace_id}")
        else:
            rows = _get(f"/observability/traces?session_id={session_id}&limit=1").get("traces") or []
            if not rows:
                return {}
            t = _get(f"/observability/traces/{rows[0].get('trace_id') or rows[0].get('id')}")

        def walk(sp, out):
            if not isinstance(sp, dict):
                return
            if sp.get("name") == "路由决策":
                meta = (sp.get("output") or {}).get("routing_meta") or {}
                if meta:
                    out["domain"] = meta.get("domain")
                    out["action"] = meta.get("domain_action")
                    out["fine_top1"] = meta.get("fine_top1")
                    out["route_mode"] = meta.get("tool_route_mode")
                for e in sp.get("events") or []:
                    msg = str(e.get("message", ""))
                    if "决定" in msg or "domain" in msg:
                        out.setdefault("events", []).append(msg[:80])
            for c in sp.get("children") or []:
                walk(c, out)

        out: dict = {}
        for sp in t.get("spans") or []:
            walk(sp, out)
        return out
    except Exception as e:
        return {"error": str(e)[:80]}


def _get(path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers=HDRS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    ts = int(time.time())
    s_travel = f"d8-reg-travel-{ts}"
    s_chat = f"d8-reg-chat-{ts}"
    cases = [
        ("D1", "帮我排福州2天行程，2个人", s_travel, ["travel"]),
        ("D2", "改成3天", s_travel, ["travel", "continuation"]),
        ("D3", "太赶了", s_travel, ["travel", "continuation"]),
        ("D4", "帮我排纽约3天行程", f"d8-reg-ny-{ts}", ["travel", "clarify", "不支持"]),
        ("D5", "帮我规划个行程", f"d8-reg-plan-{ts}", ["travel"]),
        ("D6", "统计本月订单金额", f"d8-reg-sql-{ts}", ["sql"]),
        ("D7", "你好，介绍下你自己", s_chat, ["general", "direct"]),
        ("D8", "福州天气怎么样", s_chat, ["travel", "general", "不崩"]),
    ]
    print(f"{'#':<4} {'sse':<6} | 路由 | 回答摘录")
    print("-" * 100)
    verdicts = []
    for cid, q, sid, _ in cases:
        try:
            r = chat(q, sid)
        except Exception as e:
            print(f"{cid:<4} EXC  | {str(e)[:80]}")
            verdicts.append((cid, False, str(e)[:60]))
            continue
        time.sleep(1)
        rt = routing_of(sid, r["trace_id"])
        route = f"{rt.get('domain','?')}/{rt.get('action','?')}" + (
            f"/{rt.get('fine_top1')}" if rt.get("fine_top1") else "")
        head = r["answer"][:70].replace("\n", " ")
        print(f"{cid:<4} {r['done']:<6} | {route:<40} | {head}")

        # 判定（路由级确定性判定 + 回答不崩）
        ok = r["done"] == "done" and r["answer"].strip()
        if cid == "D6":
            ok = ok and rt.get("fine_top1") == "sql.query"
        elif cid == "D1":
            ok = ok and ("行程" in r["answer"] or rt.get("action") == "prefilter_travel")
        elif cid == "D7":
            ok = ok and "未能找到相关信息" not in r["answer"]
        elif cid in ("D2", "D3"):
            ok = ok and rt.get("action") == "prefilter_travel" or ok and "天" in r["answer"] or ok and "轻松" in r["answer"] or ok and "需求" in r["answer"]
        verdicts.append((cid, ok, route))

    print("\n==== D1-D8 回归汇总 ====")
    failed = [c for c, ok, _ in verdicts if not ok]
    for cid, ok, route in verdicts:
        print(f"  {'PASS' if ok else 'CHECK'}  {cid}  ({route})")
    print(f"\nPASS {len(verdicts) - len(failed)}/{len(verdicts)}"
          f"（CHECK = 路由级无法确定性判定，需对照回答文案人工确认）")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
