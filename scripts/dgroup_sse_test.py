# -*- coding: utf-8 -*-
"""tmp/dgroup_test.py — D 组对话路径实机测试（SSE 全链路）

走真实生产链路：浏览器等价客户端 → BFF(:3100) → APISIX(:9080) → 主图 Router(LLM 分诊) → 域图。
每个场景独立 SSE 会话（D1-D3 共用同一 session_id 验证跨轮）。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:3100"
KEY = ""
TOKEN = ""

for line in open("frontend/.env.local", encoding="utf-8"):
    if line.startswith("API_KEY="):
        KEY = line.split("=", 1)[1].strip()

req = urllib.request.Request(BASE + "/api/auth/login",
    data=json.dumps({"username": "13897654321", "password": "Travel#2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
TOKEN = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["token"]


def chat_stream(question: str, session_id: str, timeout_s: int = 150) -> dict:
    """发 SSE 对话，返回 {status, answer, events}。"""
    payload = json.dumps({
        "question": question, "session_id": session_id,
        "kb_id": "default",
    }).encode("utf-8")
    request = urllib.request.Request(BASE + "/api/chat/stream", data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN,
                 "X-API-Key": KEY, "Accept": "text/event-stream"}, method="POST")
    answer_parts, events, status = [], [], "unknown"
    start = time.time()
    try:
        resp = urllib.request.urlopen(request, timeout=timeout_s)
        status = resp.status
        buf = b""
        while time.time() - start < timeout_s:
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                text = frame.decode("utf-8", errors="replace")
                etype, data = None, ""
                for line in text.splitlines():
                    if line.startswith("event:"):
                        etype = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if not etype:
                    continue
                events.append(etype)
                try:
                    d = json.loads(data)
                except Exception:
                    d = {"raw": data}
                if etype == "delta":
                    answer_parts.append(d.get("text") or d.get("content") or "")
                elif etype == "done":
                    if isinstance(d, dict):
                        answer_parts.append(d.get("final_answer") or d.get("answer") or "")
                    status = "done"
                    return {"status": status, "answer": "".join(answer_parts),
                            "events_tail": events[-8:]}
                elif etype == "error":
                    status = "error"
                    return {"status": "error", "answer": "".join(answer_parts),
                            "error": d, "events_tail": events[-8:]}
        return {"status": "timeout", "answer": "".join(answer_parts),
                "events_tail": events[-8:]}
    except urllib.error.HTTPError as e:
        return {"status": "http_%d" % e.code, "answer": "",
                "error": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:
        return {"status": "exception", "answer": "".join(answer_parts),
                "error": "%s: %s" % (type(e).__name__, e)}


SCENARIOS = [
    ("D1", "dtest-ui-1", "帮我排福州2天行程，2个人"),
    ("D2", "dtest-ui-1", "改成3天"),
    ("D3", "dtest-ui-1", "太赶了"),
    ("D4", "dtest-ui-2", "帮我排纽约3天行程"),
    ("D5", "dtest-ui-3", "帮我规划个行程"),
    ("D6", "dtest-ui-4", "统计本月订单金额"),
    ("D7", "dtest-ui-5", "你好，介绍下你自己"),
    ("D8", "dtest-ui-6", "福州天气怎么样"),
]

out = open("tmp/dgroup_results.json", "w", encoding="utf-8")
results = {}
for sid_tag, sess, q in SCENARIOS:
    print("=== %s: %s ===" % (sid_tag, q), flush=True)
    r = chat_stream(q, sess)
    results[sid_tag] = {"question": q, **r}
    print("status:", r["status"], flush=True)
    ans = r["answer"]
    print("answer[%d]:" % len(ans), ans[:400].replace("\n", " ⏎ "), flush=True)
    if r.get("error"):
        print("error:", str(r["error"])[:200], flush=True)
    out.write(json.dumps(results, ensure_ascii=False, indent=1))
    out.flush()
out.close()
print("ALL DONE")
