"""context_budget Phase 2 实机验证脚本 — 通过 APISIX 真实链路验证 MVP

用法（宿主机直接跑，走 9080 网关）：
    python scripts/context_budget_verify.py caseA      # 短问答：期望 L1/L3/L4 不触发
    python scripts/context_budget_verify.py caseB      # RAG 大输出：期望 L1 可能触发
    python scripts/context_budget_verify.py caseC      # 长会话多轮：期望 L2/Preflight 可能触发
    python scripts/context_budget_verify.py metrics    # 快照三个 context 指标

输出全部带 [VERIFY] 前缀，供人工核对；不做自动断言（真实 LLM 行为有波动）。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://localhost:9080"
USERNAME = "13897654321"
PASSWORD = "Travel#2026"


def _api_key() -> str:
    """服务级 X-API-Key（与 frontend/.env.local 同源，D 组实机脚本同先例）。"""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "frontend", ".env.local"),):
        try:
            for line in open(cand, encoding="utf-8"):
                if line.startswith("API_KEY="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def _login() -> str:
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["token"]


def stream_chat(token: str, session_id: str, question: str, timeout: float = 120):
    """真实 POST /chat/stream，返回 (事件列表, 耗时秒)。"""
    req = urllib.request.Request(
        BASE + "/api/chat/stream",
        data=json.dumps({"question": question, "session_id": session_id,
                         "request_id": f"ctxv-{int(time.time()*1000)}"}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "X-API-Key": _api_key(),
                 "Accept": "text/event-stream"}, method="POST")
    t0 = time.monotonic()
    events: list[tuple[str, dict]] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        evt_name, data_buf = None, []
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
            if line.startswith("event:"):
                evt_name = line[6:].strip()
            elif line.startswith("data:") and evt_name:
                try:
                    events.append((evt_name, json.loads(line[5:].strip())))
                except ValueError:
                    events.append((evt_name, {"_raw": line[5:].strip()[:200]}))
                evt_name = None
    return events, time.monotonic() - t0


def summarize(events, label: str) -> None:
    kinds: dict[str, int] = {}
    order: list[str] = []
    for name, _ in events:
        kinds[name] = kinds.get(name, 0) + 1
        if not order or order[-1] != name:
            order.append(name)
    print(f"[VERIFY:{label}] 事件统计={json.dumps(kinds, ensure_ascii=False)}")
    print(f"[VERIFY:{label}] 帧序压缩={'>'.join(dict.fromkeys(order))}")
    for name, data in events:
        if name == "context":
            print(f"[VERIFY:{label}] context事件={json.dumps(data, ensure_ascii=False)}")
        if name == "done":
            cu = data.get("context_usage")
            print(f"[VERIFY:{label}] done.context_usage={json.dumps(cu, ensure_ascii=False)}")
            print(f"[VERIFY:{label}] done.elapsed={data.get('elapsed')}s sources={len(data.get('sources') or [])}")
    answer = next((d.get("answer", "") for n, d in reversed(events)
                   if n in ("answer", "_answer_event")), "")
    delta_text = "".join(d.get("content", "") for n, d in events if n == "delta")
    final = answer or delta_text
    print(f"[VERIFY:{label}] 回答长度={len(final)} chars 尾部={final[-60:]!r}")


def metrics_snapshot() -> dict[str, float]:
    out: dict[str, float] = {}
    with urllib.request.urlopen("http://localhost:8000/metrics", timeout=8) as resp:
        for line in resp.read().decode("utf-8", "replace").splitlines():
            for key in ("context_compactions_total", "context_tokens_saved_total",
                        "context_budget_overflow_total"):
                if line.startswith(key) and "level" in line:
                    try:
                        out[line.split("{")[1].split("}")[0] + "|" + key] = float(
                            line.rsplit(" ", 1)[1])
                    except (IndexError, ValueError):
                        pass
    return out


def print_metrics(tag: str, snap: dict[str, float]) -> None:
    print(f"[VERIFY:{tag}] metrics({len(snap)} series):")
    for k in sorted(snap):
        print(f"    {k} = {snap[k]:g}")


def run_case(tag: str, session_id: str, questions: list[str], token: str) -> None:
    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        try:
            events, elapsed = stream_chat(token, session_id, q)
        except Exception as e:  # noqa: BLE001 — 验证脚本要看到原始异常
            print(f"[VERIFY:{tag}] 第{i}问失败: {type(e).__name__}: {e}")
            return
        print(f"[VERIFY:{tag}] 第{i}问 耗时={elapsed:.1f}s 问={q[:30]!r}")
        summarize(events, f"{tag}#{i}")


def main() -> None:
    case = sys.argv[1] if len(sys.argv) > 1 else "caseA"
    token = _login()
    print("[VERIFY] 登录成功")

    if case == "metrics":
        print_metrics("metrics", metrics_snapshot())
        return

    if case == "caseA":
        run_case("caseA", "ctx-verify-a",
                 ["你好，请用一句话介绍一下你自己。"], token)
    elif case == "caseB":
        # 指向已补齐的 cs_* 知识库（cs_faq/cs_aftersales 等），诱导 rag.search
        # 返回大段知识文本，观察 L1 是否触发
        run_case("caseB", "ctx-verify-b",
                 ["请把知识库里关于商品保修、发票开具、支付配送、退换货的完整政策内容都列出来，越详细越好。"],
                 token)
    elif case == "caseC":
        turns = [f"这是测试第{i}句话，请简单回复一句就好。" for i in range(1, 11)]
        run_case("caseC", "ctx-verify-c", turns + [
            "综合我们前面聊过的全部内容，请详细总结一下你了解到的商品保修、发票和配送政策，"
            "并把知识库里能查到的相关条款都整理出来。",
        ], token)
    else:
        print(f"unknown case {case}")


if __name__ == "__main__":
    main()
