# -*- coding: utf-8 -*-
"""obsrecon_e2e_round3.py — Phase 5 第三轮：主图路由埋点复活验证 + 主图 RAG + 多轮长会话。

前置：RoutingEngine 决策埋点（record_routing_engine_decision）已随 app 重建生效；
rag-service 已挂 /metrics 且被 Prometheus 抓取。
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

from obsrecon_e2e import chat_sse, login  # noqa: E402

LONG1 = ("请直接给出分析结论，不要向我追问任何信息。假设你是电商运营顾问：双11预售期客服咨询量通常比日常高多少倍？"
         "高频问题集中在哪些类别？请直接给出经验数值与依据，并列出三个可执行的预处理动作。")
LONG2 = ("继续上一个问题，不要追问。请基于预售期的数据特征，推演开售日当天零点到六点的咨询脉冲曲线形态，"
         "给出坐席弹性排班的具体比例建议，并说明智能客服在这六个小时内应采取的分级承接策略。")
LONG3 = ("继续，不要追问。请总结前两轮的分析，输出一张三阶段（预售/开售/返场）对照表：每阶段列出咨询量倍率、"
         "高频问题TOP3、自动化承接目标、人力弹性系数四行指标，最后给出整套方案的落地风险清单。")


def prom_query(expr: str) -> list:
    url = "http://127.0.0.1:9090/api/v1/query?query=" + urllib.parse.quote(expr)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return __import__("json").loads(r.read())["data"]["result"]
    except Exception:
        return []


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--username", default="local_super_admin")
    args = ap.parse_args()
    token, user_id, tenant_id = login(args.username, args.password)
    print(f"login OK user={user_id}")

    cases = [
        ("p3-general-chat", "给我讲一个关于坚持的简短小故事"),
        ("p3-tool-weather", "查一下北京今天的天气怎么样"),
        ("p3-rag", "帮我从知识库查一下客户投诉处理的标准流程和话术规范"),
        ("p3-embedding", "梳理一下退货率偏高的商品有什么共同点"),
        ("p3-plan", "分析上个月华东销售额下滑的原因并生成一份带图表的经营分析报告"),
    ]
    for name, q in cases:
        r = chat_sse(token, user_id, tenant_id, name, q)
        print(f"{name}: status={r['status']} elapsed={r['elapsed']}s "
              f"events={list(r['events'].items())[:8]}")
        if r.get("answer"):
            print(f"   A: {r['answer'][:70]}")

    print("\n--- 多轮长会话（同 session，触发上下文压缩）---")
    for i, q in enumerate([LONG1, LONG2, LONG3]):
        r = chat_sse(token, user_id, tenant_id, "p3-context", q)
        print(f"  [{i}] status={r['status']} elapsed={r['elapsed']}s events={list(r['events'].items())[:8]}")

    time.sleep(25)
    print("\n证据核对（increase[30m] > 0）")
    evidence = [
        ("router_layer_total", 'sum(increase(router_layer_total[30m])) by (layer)'),
        ("router_decision_total", 'sum(increase(router_decision_total[30m])) by (mode)'),
        ("router_confidence", 'increase(router_confidence_count[30m])'),
        ("routing_domain_total", 'sum(increase(routing_domain_total[30m])) by (domain, source)'),
        ("routing_hierarchy_verdict", 'sum(increase(routing_hierarchy_verdict_total[30m])) by (verdict)'),
        ("routing_latency", 'sum(increase(routing_latency_seconds_count[30m])) by (stage)'),
        ("rag_query_total(rag-service)", 'sum(increase(rag_query_total{job="rag-service"}[30m])) by (status)'),
        ("rag_stage_duration(rag-service)", 'sum(increase(rag_stage_duration_seconds_count{job="rag-service"}[30m])) by (stage)'),
        ("context_compactions", 'sum(increase(context_compactions_total[30m])) by (level)'),
        ("context_tokens_by_component", 'sum(increase(context_tokens_by_component_total[30m])) by (component)'),
        ("context_tokens_saved", 'sum(increase(context_tokens_saved_total[30m])) by (level)'),
    ]
    ok = 0
    for label, expr in evidence:
        rs = prom_query(expr)
        hit = any(float(s["value"][1]) > 0 for s in rs)
        ok += hit
        vals = ["%s=%s" % (",".join(f"{k}={v}" for k, v in s["metric"].items()
                                    if k not in ("instance", "job")), s["value"][1]) for s in rs]
        print(f"  {'HIT ' if hit else 'MISS'} {label:<30} {'; '.join(vals)[:100]}")
    print(f"ROUND3_EVIDENCE_PASS={ok}/{len(evidence)}")


if __name__ == "__main__":
    main()
