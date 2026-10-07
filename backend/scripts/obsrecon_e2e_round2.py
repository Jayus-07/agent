# -*- coding: utf-8 -*-
"""obsrecon_e2e_round2.py — Phase 5 第二轮精准流量（主图路由 / RAG API 直调 / 多轮长会话）。

第一轮教训：多数问题被旅游 prefilter / clarify 截走，未到达主图 RoutingEngine 与
RAG 链。本轮用精准措辞与直调端点补齐：
  r2_llm_router   主图 LLM 意图路由 → router_layer_total{layer=llm} + router_confidence
  r2_embedding    主图 embedding 层 → router_layer_total{layer=embedding}
  r2_workflow     主图 plan 路由     → router_decision_total{mode=plan|workflow}
  r2_tool_direct  主图 direct+FC    → tool_selector_total{source=fc} + agent_tool_calls_total
  r2_rag_api      /api/rag 直调     → rag_query_total + rag_stage_duration_seconds
  r2_context_1..3 同一 session 三轮长文 → context_compactions_total / tokens_by_component_total
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

from obsrecon_e2e import chat_sse, login, prom_increased  # noqa: E402

GATEWAY = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
TAG = "obsrecon-test"

LONG_Q1 = ("请系统性地分析电商平台双十一种预售期、开售日、返场期三个阶段的客服咨询量波动规律，"
           "包括每个阶段的用户心理特征、高频问题类型分布、以及对应的智能客服自动化承接策略建议，"
           "要求分别给出人力排班弹性系数和知识库覆盖率的建设目标。")
LONG_Q2 = ("在上一条分析的基础上，请继续展开：直播带货场景中咨询量瞬时脉冲的形成机制是什么？"
           "与日销期的稳态咨询有哪些结构性差异？平台侧应该如何用并发准入、上下文压缩、"
           "分级降级三层手段控制峰值时段的服务成本与服务质量平衡？请给出可落地的参数建议。")
LONG_Q3 = ("继续深化：如果把上述策略落到一个 200 坐席、日均 5 万次咨询的中型电商客服团队，"
           "请推演第一、二、三季度的分阶段改造路线图，每个季度给出自动化承接率、"
           "首响时长、人力成本节省三个指标的量化目标，并说明如何用监控指标验证策略有效性。")


def rag_ask(token: str, question: str) -> dict:
    body = {"question": question, "kb_id": "travel"}
    req = urllib.request.Request(f"{GATEWAY}/api/rag",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    if os.getenv("API_KEY"):
        req.add_header("X-API-Key", os.getenv("API_KEY"))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return {"status": 200, "keys": list(data.keys())[:6],
                "answer_head": str(data.get("answer") or data.get("data", {}).get("answer") or "")[:100],
                "elapsed": round(time.time() - t0, 1)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:
        return {"status": 0, "error": str(e)[:200]}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--username", default="local_super_admin")
    args = ap.parse_args()

    token, user_id, tenant_id = login(args.username, args.password)
    print(f"login OK user={user_id}")

    print("\n=== r2_llm_router（主图 LLM 路由）===")
    r = chat_sse(token, user_id, tenant_id, "r2-llm-router",
                 "分析一下最近新能源汽车市场的竞争格局，重点对比头部厂商的销量与智能化战略差异")
    print(f"  status={r['status']} elapsed={r['elapsed']}s events={r['events']}")

    print("\n=== r2_embedding（主图 embedding 层）===")
    r = chat_sse(token, user_id, tenant_id, "r2-embedding",
                 "帮我梳理一下库存周转天数异常偏高的那些 SKU 的共同特征")
    print(f"  status={r['status']} elapsed={r['elapsed']}s events={r['events']}")

    print("\n=== r2_workflow（plan/workflow 路由）===")
    r = chat_sse(token, user_id, tenant_id, "r2-workflow",
                 "统计各个仓库的库存周转率，找出低于行业均值的产品并生成一份补货优先级报告")
    print(f"  status={r['status']} elapsed={r['elapsed']}s events={r['events']}")

    print("\n=== r2_tool_direct（主图 direct + FC 选 Tool）===")
    r = chat_sse(token, user_id, tenant_id, "r2-tool",
                 "帮我查一下明天福州的天气怎么样，适不适合户外活动")
    print(f"  status={r['status']} elapsed={r['elapsed']}s events={r['events']}")

    print("\n=== r2_rag_api（/api/rag 直调 RAG 链）===")
    for i, question in enumerate([
        "福州三坊七巷有什么必吃的小吃？",
        "鼓山游览路线怎么安排比较合理？",
        "知识库无关问题：量子退相干在冷链物流温控中的工程参数是多少？",
    ]):
        r = rag_ask(token, question)
        print(f"  [{i}] status={r['status']} elapsed={r.get('elapsed')}s "
              f"answer={r.get('answer_head', r.get('error', ''))[:70]}")
        time.sleep(2)

    print("\n=== r2_context（同 session 三轮长文触发压缩）===")
    for i, question in enumerate([LONG_Q1, LONG_Q2, LONG_Q3]):
        r = chat_sse(token, user_id, tenant_id, "r2-context", question)
        print(f"  [{i}] status={r['status']} elapsed={r['elapsed']}s events={list(r['events'].items())[:8]}")

    time.sleep(20)  # 等 Prometheus 抓取周期
    print("\n证据核对（近 40m increase > 0）")
    evidence = [
        ("router_layer_total{layer=llm}", 'increase(router_layer_total{layer="llm"}[40m])'),
        ("router_layer_total{layer=embedding}", 'increase(router_layer_total{layer="embedding"}[40m])'),
        ("router_layer_total{layer=rule}", 'increase(router_layer_total{layer="rule"}[40m])'),
        ("router_decision_total", 'sum(increase(router_decision_total[40m])) by (mode)'),
        ("router_confidence", 'increase(router_confidence_count[40m])'),
        ("routing_domain_total", 'sum(increase(routing_domain_total[40m])) by (domain)'),
        ("tool_selector_total{fc}", 'increase(tool_selector_total{source="fc"}[40m])'),
        ("agent_tool_calls_total", 'sum(increase(agent_tool_calls_total[40m])) by (tool, status)'),
        ("agent_tool_latency", 'sum(increase(agent_tool_latency_seconds_count[40m])) by (tool)'),
        ("rag_query_total", 'sum(increase(rag_query_total[40m])) by (status)'),
        ("rag_stage_retrieve", 'increase(rag_stage_duration_seconds_count{stage="retrieve"}[40m])'),
        ("rag_stage_generate", 'increase(rag_stage_duration_seconds_count{stage="generate"}[40m])'),
        ("rag_stage_total_m", 'increase(rag_stage_duration_seconds_count{stage="total"}[40m])'),
        ("rag_stage_rerank", 'increase(rag_stage_duration_seconds_count{stage="rerank"}[40m])'),
        ("context_compactions", 'sum(increase(context_compactions_total[40m])) by (level)'),
        ("context_tokens_by_component", 'sum(increase(context_tokens_by_component_total[40m])) by (component)'),
        ("agent_stage_total", 'sum(increase(agent_stage_total[40m])) by (domain, stage)'),
        ("agent_stage_duration", 'sum(increase(agent_stage_duration_seconds_count[40m])) by (stage)'),
    ]
    ok = 0
    for label, expr in evidence:
        got = prom_increased(expr.split("increase(")[-1].split("[")[0], minutes=40) if False else None
        # 直接查具体表达式
        import obsrecon_e2e as base
        rs = base_prom_query(expr)
        hit = any(float(s["value"][1]) > 0 for s in rs)
        ok += hit
        vals = ["%s=%s" % (",".join(f"{k}={v}" for k, v in s["metric"].items()
                                    if k not in ("instance", "job")), s["value"][1]) for s in rs]
        print(f"  {'HIT ' if hit else 'MISS'} {label:<36} {'; '.join(vals)[:100]}")
    print(f"ROUND2_EVIDENCE_PASS={ok}/{len(evidence)}")


def base_prom_query(expr: str) -> list:
    url = f"http://127.0.0.1:9090/api/v1/query?query=" + urllib.parse.quote(expr)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())["data"]["result"]
    except Exception:
        return []


if __name__ == "__main__":
    main()
