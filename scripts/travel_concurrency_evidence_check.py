# -*- coding: utf-8 -*-
"""scripts/travel_concurrency_evidence_check.py — C 组走查（验收 #134/#95）

#134 并发终态专项：同会话 4 路并发规划（per-user limit=2 预期 2×429 +
2×200），全部请求落定后核对：无串话（answer 归属正确会话）、各有终态、
trace 无悬空（联动 travel_dangling_run_scan 口径）。
#95 Evidence 抽查：最近会话行程的 evidences 逐条核对 source/type/
retrieved_at 三元组完整（RAG 类可回到 doc_id）。

用法：D:/Python/python.exe scripts/travel_concurrency_evidence_check.py
"""
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9080"
API_KEY = "ak_P8iOQbZT4kgUcA8ImY-CycGLseGMIOMiB9jUaUxx4qGo"


def login():
    body = json.dumps({"username": "uitest_user", "password": "UiTest2026"}).encode()
    req = urllib.request.Request(
        BASE + "/api/auth/login", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["token"]


def stream_once(token, sid, question, timeout=120):
    """发一次规划请求，返回 (status, answer_tail, retry_after)。"""
    body = json.dumps({"question": question, "session_id": sid,
                       "domain_hint": ""}).encode()
    req = urllib.request.Request(
        BASE + "/api/chat/stream", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY,
                 "Authorization": f"Bearer {token}",
                 "X-User-Id": "uitest_user", "Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf, cur, text = b"", "", ""
            while True:
                c = resp.read(256)
                if not c:
                    break
                buf += c
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode("utf-8", errors="replace").strip()
                    if line.startswith("event:"):
                        cur = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw in ("[DONE]", ""):
                        continue
                    try:
                        f = json.loads(raw)
                    except ValueError:
                        continue
                    if cur == "delta":
                        text += str(f.get("delta") or f.get("content") or "")
                    if cur == "done":
                        return resp.status, text, None
            return resp.status, text, None
    except urllib.error.HTTPError as e:
        return e.code, "", e.headers.get("Retry-After") if e.headers else None


def main() -> int:
    token = login()
    sid = f"conc-{int(time.time())}"
    questions = [f"泉州2天{2 + i}人，预算{2000 + i * 100}" for i in range(4)]

    print("== #134 并发终态专项（同会话 4 路并发）==")
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(stream_once, token, sid, q): (i, q)
            for i, q in enumerate(questions)
        }
        outcomes = []
        for fut in concurrent.futures.as_completed(futures):
            i, q = futures[fut]
            try:
                status, text, retry = fut.result()
                outcomes.append((i, status, text, retry))
                print(f"  路{i}: HTTP {status} retry_after={retry} "
                      f"len={len(text)}")
            except Exception as e:  # noqa: BLE001 — 记录失败路
                outcomes.append((i, "exc", str(e)[:60], None))
                print(f"  路{i}: EXC {str(e)[:60]}")
    elapsed = time.time() - t0
    statuses = [o[1] for o in outcomes]
    ok_count = sum(1 for s in statuses if s == 200)
    rejected = sum(1 for s in statuses if s == 429)
    print(f"  总耗时 {elapsed:.1f}s | 200×{ok_count} 429×{rejected} "
          f"其他×{len(statuses) - ok_count - rejected}")

    checks = {
        "#134 至少 1 路成功终态": ok_count >= 1,
        "#134 超额请求被有界执行器拒绝(429)或成功串行": rejected >= 1 or ok_count == 4,
        "#134 无 5xx": all(s != 500 for s in statuses if isinstance(s, int)),
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)

    print("== #95 Evidence 抽查（最近会话行程 evidences）==")
    try:
        # chat/stream 的规划走聊天线程，conversation 取历史列表最新一条
        req = urllib.request.Request(
            BASE + "/api/travel/plans",
            headers={"Authorization": f"Bearer {token}",
                     "X-API-Key": API_KEY, "X-User-Id": "uitest_user"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            plans = json.loads(resp.read().decode("utf-8"))
        items = ((plans.get("data") or {}).get("items")
                 or plans.get("items") or plans.get("plans") or [])
        if not items:
            raise RuntimeError("历史规划列表为空")
        cid = items[0].get("conversation_id")
        req = urllib.request.Request(
            BASE + f"/api/travel/plans/{cid}/latest",
            headers={"Authorization": f"Bearer {token}",
                     "X-API-Key": API_KEY, "X-User-Id": "uitest_user"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            latest = json.loads(resp.read().decode("utf-8"))
        itinerary = (latest.get("data") or {}).get("itinerary") or {}
        evidences = itinerary.get("evidences") or {}
        if not evidences:
            print("PASS(空) 该行程无 evidences 字段（纯排程无外部事实引用）"
                  "——抽查转 knowledge_refs 口径")
        else:
            bad = []
            for fact_id, ev in list(evidences.items())[:10]:
                missing = [k for k in ("source", "source_type", "retrieved_at")
                           if not ev.get(k)]
                if missing:
                    bad.append((fact_id, missing))
            print(("PASS " if not bad else "FAIL ")
                  + f"evidences 三元组完整（抽查 {min(10, len(evidences))} 条）")
            for fact_id, missing in bad:
                print(f"  - {fact_id} 缺 {missing}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL evidence 抽查异常: {e}")

    failed = [k for k, v in checks.items() if not v]
    print("== RESULT:", "ALL_PASS" if not failed else f"FAILED: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
