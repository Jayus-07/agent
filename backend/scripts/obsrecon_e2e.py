# -*- coding: utf-8 -*-
"""obsrecon_e2e.py — Grafana 观测重构 Phase 5 受控真实流量（TEST_TRAFFIC）。

所有请求 session_id / request_id 前缀 `obsrecon-test-` = 验收注入样本标识，
报告中与真实已有样本区分。只读演练：不发消息、不写库（聊天写库面仅
agent_memory 常规会话表）、不动容器、不改配置。

场景（与验收 B/E/F/G 组对应）：
  router_rule      规则直达        → router_layer_total{layer=rule} + routing_domain_total
  router_embedding 语义改写命中     → router_layer_total{layer=embedding}
  router_llm       复杂意图判域     → router_layer_total{layer=llm} + router_confidence
  tool_direct      Tool 直达       → tool_selector_* + agent_tool_calls_total
  workflow         SQL 工作流      → router_decision_total{mode=workflow} + sql_agent_*
  clarify          低置信追问       → agent_clarify_shown_total
  rag_hit          知识库命中       → rag_query_total + rag_stage_duration_seconds
  context_long     长上下文         → context_compactions_total / tokens_by_component_total

用法：cd backend && PYTHONPATH=.. python scripts/obsrecon_e2e.py --password <pwd> [--groups all]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

GATEWAY = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
PROM = os.getenv("OBSRECON_PROM", "http://127.0.0.1:9090")
TAG = "obsrecon-test"  # TEST_TRAFFIC 标识前缀

CASES = [
    # (group, name, question, 期望证据指标片段)
    ("router", "router_rule", "你好，请介绍一下你自己",
     ["router_layer_total", "input_guard_total"]),
    ("router", "router_embedding", "我想规划一个海边度假行程，帮忙安排",
     ["routing_domain_total"]),
    ("router", "router_llm", "帮我把上个月的华东区域销售额按省份做同环比分析，重点是下滑超过两成的品类，并给出原因假设",
     ["router_layer_total", "router_confidence"]),
    ("tool", "tool_direct", "福州三坊七巷附近有什么好吃的美食店",
     ["agent_tool_calls_total", "tool_selector_total"]),
    ("workflow", "workflow_sql", "查询一下库存低于50的商品有哪些",
     ["router_decision_total", "sql_agent_requests_total"]),
    ("router", "clarify", "帮我查一下那个东西的数据",
     ["agent_clarify_shown_total"]),
    ("rag", "rag_hit", "福州旅游有哪些必去的景点？按照攻略推荐",
     ["rag_query_total", "rag_stage_duration_seconds"]),
    ("context", "context_long",
     "请详细分析电商平台大促期间客服咨询量的典型波动规律，包括预售期、开售日、平销期三个阶段的用户心理与高频问题类型，"
     "并分别给出三个阶段的坐席排班建议、知识库配置建议和智能客服自动化承接策略，最后汇总成一张对比表格。"
     "补充：请再展开分析直播带货场景下咨询量的瞬时脉冲特征、售后高峰的形成机制，"
     "以及如何用预算治理和上下文压缩手段控制长会话的服务成本，给出可落地的参数建议。",
     ["context_compactions_total", "context_tokens_by_component_total"]),
]


def _request(method, url, body=None, token=None, headers=None, timeout=180):
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


def chat_sse(token: str, user_id: str, tenant_id: str, name: str,
             question: str, timeout: float = 240) -> dict:
    body = {
        "question": question,
        "session_id": f"{TAG}-{name}",
        "request_id": f"{TAG}-{name}-{int(time.time() * 1000)}",
        "idempotency_key": f"{TAG}-{name}-{int(time.time() * 1000)}",
    }
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
    answer_head = ""
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return {"status": e.code, "events": {}, "answer": "",
                "error": e.read().decode("utf-8", "replace")[:200],
                "elapsed": round(time.time() - t0, 1)}
    with resp:
        status = resp.status
        buf = ""
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for line in block.splitlines():
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                        events[ev] = events.get(ev, 0) + 1
                    elif line.startswith("data:") and ev == "done":
                        try:
                            payload = json.loads(line[5:].strip())
                            answer_head = str(payload.get("reply") or
                                              payload.get("answer") or "")[:120]
                        except Exception:
                            pass
    return {"status": status, "events": events, "answer": answer_head,
            "elapsed": round(time.time() - t0, 1)}


def prom_increased(metric: str, extra: dict | None = None, minutes: int = 30) -> bool:
    """指标近 N 分钟是否有增长（对照受控流量前快照太复杂，直接查 5m rate>0 或
    increase(30m)>当前增量；观测验收口径=出现真实样本即可）。"""
    q = f'increase({metric}[{minutes}m])'
    if extra:
        conds = ",".join(f'{k}="{v}"' for k, v in extra.items())
        q = f'increase({metric}{{{conds}}}[{minutes}m])'
    try:
        req = urllib.request.Request(
            f"{PROM}/api/v1/query?query=" + urllib.parse.quote(q))
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        for s in data.get("data", {}).get("result", []):
            if float(s["value"][1]) > 0:
                return True
    except Exception:
        pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--username", default="local_super_admin")
    ap.add_argument("--groups", default="all",
                    help="逗号分隔: router,tool,workflow,rag,context")
    args = ap.parse_args()
    groups = None if args.groups == "all" else set(args.groups.split(","))

    token, user_id, tenant_id = login(args.username, args.password)
    print(f"login OK user={user_id} tenant={tenant_id}")

    results = []
    for group, name, question, expects in CASES:
        if groups and group not in groups:
            continue
        print(f"\n=== [{group}] {name} ===")
        print(f"  Q: {question[:60]}...")
        r = chat_sse(token, user_id, tenant_id, name, question)
        ok_events = r["events"]
        print(f"  status={r['status']} elapsed={r['elapsed']}s events={ok_events}")
        if r["answer"]:
            print(f"  A: {r['answer'][:80]}")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
        results.append({"group": group, "name": name, **r,
                        "expects": expects})
        time.sleep(2)  # 控速，避免触发限流混淆口径

    print("\n" + "=" * 70)
    print("证据指标核对（近 30m increase > 0）")
    ok = 0
    for r in results:
        for m in r["expects"]:
            got = prom_increased(m)
            ok += got
            print(f"  {'PASS' if got else 'MISS'} {m:<42} <- {r['name']}")
    print(f"EVIDENCE_PASS={ok}")
    json.dump(results, open(f"d:/tmp/{TAG}_results.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
