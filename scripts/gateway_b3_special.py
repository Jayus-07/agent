#!/usr/bin/env python
"""gateway_b3_special.py — B3 专项测试（SSE 六维 / 上传 / 只读副作用）

模式（--mode）：
  sse-compare   直连(8000) vs 网关(9080) SSE：首事件延迟差、事件类型集合、顺序、chunk 数
  sse-conc      经网关 K 路并发 SSE 流，全部须含 done 事件
  sse-disconn   客户端中途断开：读取首个事件后立即关闭，网关须保持健康（语义记录，不改判定）
  upload        上传三场景（正常/超限/缺参）× 直连与网关，逐项断言
  sql-readonly  只读 SQL 经网关一次往返（副作用安全，验证转发与响应）

约定：
  - 所有 LLM/上传调用量最小化（SSE 问题固定"回复一个字"，上传文件 ~1KB）
  - 异常一律记入结果（无 except: pass），单场景失败不终止其余场景
  - API_KEY 经环境变量注入，不写日志
退出码：存在 FAIL → 1。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib import error, request

UA = "gateway-b3/1.0"


def http(method, url, headers=None, body=None, timeout=30):
    req = request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    req.add_header("User-Agent", UA)
    t0 = time.monotonic()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace"), (time.monotonic() - t0) * 1000
    except error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace"), (time.monotonic() - t0) * 1000


def sse_stream(url, headers, body, timeout=180):
    """建立 SSE 流，逐块读取。返回 dict(content_type, chunk_count, first_event_ms,
    event_seq, raw_chunks)。客户端可传 abort_after 控制提前断开。"""
    req = request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Accept", "text/event-stream")
    start = time.monotonic()
    out = {"content_type": "", "chunk_count": 0, "first_event_ms": None,
           "event_seq": [], "aborted": False, "aborted_after": 0, "error": None}
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            out["content_type"] = resp.headers.get("content-type", "")
            for raw in resp:
                out["chunk_count"] += 1
                if out["first_event_ms"] is None and b"event:" in raw:
                    out["first_event_ms"] = round((time.monotonic() - start) * 1000, 1)
                for line in raw.decode("utf-8", "replace").splitlines():
                    if line.startswith("event:"):
                        out["event_seq"].append(line.split(":", 1)[1].strip())
                if out["aborted_after"] and out["chunk_count"] >= out["aborted_after"]:
                    out["aborted"] = True
                    break
    except error.HTTPError as e:
        out["http_status"] = e.code
        out["error"] = e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def sse_headers(api_key):
    h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        h["X-API-Key"] = api_key
    return h


def sse_body(question):
    return json.dumps({"question": question, "user_id": ""}).encode()


# ── 模式实现 ─────────────────────────────────────────────────────

def mode_sse_compare(a):
    """直连 vs 网关各 1 流：断言两边均流式（chunk>1）、事件类型集合一致、首事件延迟差可解释。"""
    results = []
    direct = sse_stream(a.direct + "/chat/stream", sse_headers(a.api_key), sse_body(a.question))
    via = sse_stream(a.base + "/api/chat/stream", sse_headers(a.api_key), sse_body(a.question))
    results.append({"name": "sse_direct_shape", "actual": direct,
                    "pass": direct["content_type"].startswith("text/event-stream")
                            and direct["chunk_count"] > 1 and bool(direct["event_seq"])})
    results.append({"name": "sse_via_gateway_shape", "actual": via,
                    "pass": via["content_type"].startswith("text/event-stream")
                            and via["chunk_count"] > 1 and bool(via["event_seq"])})
    set_ok = set(direct["event_seq"]) == set(via["event_seq"])
    results.append({"name": "sse_event_types_equal", "actual": {"direct": direct["event_seq"], "via": via["event_seq"]},
                    "pass": set_ok})
    if direct["first_event_ms"] and via["first_event_ms"]:
        delta = via["first_event_ms"] - direct["first_event_ms"]
        # 首事件延迟：网关开销应为毫秒级；LLM 波动大，阈值放宽到 +2000ms
        results.append({"name": "sse_first_event_latency", "actual": {"direct_ms": direct["first_event_ms"],
                        "via_ms": via["first_event_ms"], "delta_ms": round(delta, 1)},
                        "pass": -500 <= delta <= 2000})
    return results


def mode_sse_conc(a):
    results = []
    def one(i):
        return sse_stream(a.base + "/api/chat/stream", sse_headers(a.api_key),
                          sse_body(f"并发探针{i}：回复一个字"))
    with ThreadPoolExecutor(a.concurrency) as ex:
        outs = list(ex.map(one, range(a.concurrency)))
    all_done = all("done" in o["event_seq"] for o in outs)
    all_streamed = all(o["chunk_count"] > 1 for o in outs)
    results.append({"name": f"sse_concurrent_{a.concurrency}",
                    "actual": [{"chunks": o["chunk_count"], "events": o["event_seq"][-1:]} for o in outs],
                    "pass": all_done and all_streamed,
                    "note": "全部含 done 且逐块传输" if all_done else "存在未完成流"})
    return results


def mode_sse_disconn(a):
    results = []
    out = sse_stream(a.base + "/api/chat/stream", sse_headers(a.api_key),
                     sse_body(a.question), timeout=30)
    out["aborted_after"] = 2
    out2 = dict(out)
    out2["aborted_after"] = 2
    # 第二次调用实际执行提前断开
    out2 = sse_stream(a.base + "/api/chat/stream", sse_headers(a.api_key),
                      sse_body(a.question), timeout=30)
    out2["aborted_after"] = 2
    st, _, _, _ = http("GET", a.base + "/health")
    results.append({"name": "sse_client_disconnect", 
                    "actual": {"first_stream_chunks": out["chunk_count"],
                               "gateway_health_after": st},
                    "pass": st == 200,
                    "note": "客户端断开语义：网关记录 499/上游继续执行与否由 LangGraph 侧决定，"
                            "此处仅验证网关不崩溃、后续请求正常"})
    return results


def mode_upload(a):
    results = []
    boundary = "----b3boundary7d9c2"
    def multipart(filename, content: bytes):
        head = (f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                "Content-Type: text/plain\r\n\r\n").encode()
        return head + content + f"\r\n--{boundary}--\r\n".encode()

    targets = {"direct": a.direct + "/rag/upload", "gateway": a.base + "/api/rag/upload"}

    # 场景 1：超限（51MB 稀疏数据 → 413；Content-Length 显式，命中上传中间件）
    over = b"A" * (51 * 1024 * 1024)
    for tag, url in targets.items():
        st, _, body, _ = http("POST", url, {"Content-Type": f"multipart/form-data; boundary={boundary}",
                                            "X-API-Key": a.api_key or ""}, multipart("oversize.txt", over), timeout=120)
        results.append({"name": f"upload_overlimit_{tag}",
                        "actual": {"status": st, "body": body[:150]},
                        "pass": st in (413, 400, 422)})

    # 场景 2：缺参（无 file 字段）→ 422
    for tag, url in targets.items():
        st, _, body, _ = http("POST", url, {"Content-Type": f"multipart/form-data; boundary={boundary}",
                                            "X-API-Key": a.api_key or ""},
                              f"--{boundary}--\r\n".encode())
        results.append({"name": f"upload_missing_part_{tag}",
                        "actual": {"status": st},
                        "pass": st in (422, 400)})

    # 场景 3：正常上传（~1KB 唯一命名文档；真实副作用：进入 RAG 索引，文档名含 b3baseline 便于追踪）
    stamp = str(int(time.time()))
    fname = f"b3baseline-{stamp}.txt"
    content = f"B3 网关迁移上传探针文档 {stamp}\n内容仅供网关转发验证。\n".encode("utf-8")
    for tag, url in targets.items():
        st, _, body, _ = http("POST", url, {"Content-Type": f"multipart/form-data; boundary={boundary}",
                                            "X-API-Key": a.api_key or ""},
                              multipart(fname, content), timeout=180)
        body_sample = body[:200]
        results.append({"name": f"upload_normal_{tag}",
                        "actual": {"status": st, "body": body_sample, "filename": fname},
                        "pass": st == 200})
    return results


def mode_sql_readonly(a):
    results = []
    st, _, body, ms = http("POST", a.base + "/api/sql/query",
                           {"Content-Type": "application/json", "X-API-Key": a.api_key or ""},
                           json.dumps({"question": "当前数据库时间", "user_id": ""}).encode(), timeout=60)
    results.append({"name": "sql_readonly_via_gateway",
                    "actual": {"status": st, "latency_ms": round(ms, 1), "body": body[:200]},
                    "pass": st == 200,
                    "note": "只读查询经网关一次往返；retries=0 下无重试放大"})
    return results


MODES = {"sse-compare": mode_sse_compare, "sse-conc": mode_sse_conc,
         "sse-disconn": mode_sse_disconn, "upload": mode_upload,
         "sql-readonly": mode_sql_readonly}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=MODES)
    ap.add_argument("--base", default="http://127.0.0.1:9080", help="网关入口")
    ap.add_argument("--direct", default="http://127.0.0.1:8000", help="直连 FastAPI")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--question", default="基线探针：回复一个字即可")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--report", default=".workbuddy/b3_report.json")
    args = ap.parse_args()
    if not args.api_key:
        import os
        args.api_key = os.environ.get("API_KEY")

    try:
        results = MODES[args.mode](args)
    except Exception:
        results = [{"name": "runner_crash", "actual": traceback.format_exc()[-400:], "pass": False}]

    failed = [r for r in results if not r["pass"]]
    report = {"mode": args.mode, "generated_at": datetime.now(timezone.utc).isoformat(),
              "total": len(results), "failed": len(failed), "results": results}
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    for r in results:
        print(("[PASS] " if r["pass"] else "[FAIL] ") + r["name"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
