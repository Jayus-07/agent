"""run_context_budget_eval.py — Context Budget Evaluation CLI（Phase 4，2026-09-22）

用法（项目根目录）:
    python backend/evaluation/run_context_budget_eval.py case_a      # 普通聊天基线
    python backend/evaluation/run_context_budget_eval.py case_b      # 大工具输出（L1）
    python backend/evaluation/run_context_budget_eval.py case_c      # 长对话触发 L2-L5
    python backend/evaluation/run_context_budget_eval.py golden      # L5 事实保真 Golden Cases
    python backend/evaluation/run_context_budget_eval.py waterline   # 水线/边界/并发
    python backend/evaluation/run_context_budget_eval.py rate        # 触发率快照（Prometheus）

输出: data/eval_reports/context_budget_<mode>_<ts>.json + 控制台摘要
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "data" / "eval_reports"


def _save_report(mode: str, report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"context_budget_{mode}_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


# ── Case A：普通聊天基线（红线：L4/L5 不得触发、摘要 API = 0）──────────

def run_case_a() -> dict:
    from backend.evaluation.context_budget.driver import multi_turn

    sid = f"p4-caseA-{int(time.time())}"
    questions = [
        "你好，用一句话介绍你自己",
        "今天福州天气怎么样？",
        "帮我看看有什么商品",
        "1+1等于几",
        "谢谢",
    ]
    results = multi_turn(questions, sid)
    rows = [r.summary() for r in results]
    l4_l5 = [e for r in results for e in r.context_events
             if e.get("level") in ("L4", "L5")]
    report = {
        "mode": "case_a",
        "session_id": sid,
        "requests": len(results),
        "red_line_violations": [e for e in l4_l5],
        "red_line_ok": not l4_l5,
        "latency": {
            "total_s": [r["elapsed_s"] for r in rows],
            "ttft_s": [r["ttft_s"] for r in rows],
        },
        "context_usage_final": results[-1].done.get("context_usage"),
        "detail": rows,
    }
    path = _save_report("case_a", report)
    print(json.dumps({k: v for k, v in report.items() if k != "detail"},
                     ensure_ascii=False, indent=2))
    print(f"\nreport: {path}")
    return report


# ── Case B：大工具输出（真实 Tool 链路，验证 L1）────────────────────────

def run_case_b() -> dict:
    from backend.evaluation.context_budget.driver import chat_stream

    sid = f"p4-caseB-{int(time.time())}"
    # 诱发大输出的真实业务问题（多天行程规划 → 多 Tool → 大 step_results）
    questions = [
        "帮我规划一个 7 天的云南深度游，昆明大理丽江香格里拉都要去，"
        "每天安排 3 个以上景点，包含交通、住宿建议和每日预算明细，尽量详细",
        "在上面基础上，把每天预算明细再展开成早中晚三餐+门票+交通的分项，"
        "并给出每个景点的建议游玩时长",
    ]
    results = [chat_stream(q, sid) for q in questions]
    l1_events = [e for r in results for e in r.context_events
                 if e.get("level") == "L1"]
    l5_events = [e for r in results for e in r.context_events
                 if e.get("level") == "L5"]
    report = {
        "mode": "case_b",
        "session_id": sid,
        "turns": [r.summary() for r in results],
        "L1_events": l1_events,
        "L5_after_L1": l5_events,
        "L1_saved_total": sum(e.get("saved_tokens", 0) for e in l1_events),
        "answer_ok": all(len(r.answer) > 50 and not r.error for r in results),
    }
    path = _save_report("case_b", report)
    print(json.dumps({k: v for k, v in report.items() if k != "turns"},
                     ensure_ascii=False, indent=2))
    print(f"\nreport: {path}")
    return report


# ── Case C：长对话 + 多工具 + RAG（核心：真实触发 L2/L3/L4/L5）──────────

CASE_C_SCRIPT = [
    # 长历史铺垫（每条数百 token，多业务约束 + 标识符）
    "我要筹备一场新品发布会，场地定在福州会展中心，时间 2026-10-15 下午 2 点开始。"
    "帮我记一下筹备要点：参会嘉宾约 120 人，媒体 30 家，预算总盘子 ¥500,000，"
    "其中场地布置 ¥180,000、嘉宾差旅 ¥120,000、媒体投放 ¥100,000、机动 ¥100,000。",
    "发布会的核心产品是智能音箱 EchoPod X2，SKU 是 POD-X2-GRY-001，"
    "官方定价 ¥1,299，首发促销价 ¥1,099，促销期到 2026-11-11。"
    "毛利红线是 18%，低于这个价不能批。",
    "再记几个备选事项：开场视频由本地团队制作报价 ¥45,000；"
    "灯光音响租赁报价 ¥38,000；伴手礼预算每人 80 元。"
    "嘉宾名单里要重点标注 3 位KOL，对接人是我助理小陈。",
    "补充约束：所有物料不要使用红色主视觉，传播口径里不要提竞品名字，"
    "发布会当天所有物料 2026-10-13 之前必须进场验收。",
    "帮我在知识库里查一下以往发布会的执行复盘和场地布置的案例，尽量多找几篇，"
    "我要参考舞台尺寸、签到动线和媒体区的布置细节。",
    "把前面所有预算项加起来算一下，机动预算还剩多少，"
    "如果伴手礼超标 10% 从哪里扣比较合理。",
    "媒体投放那 100,000 里，小红书、抖音、微信各分多少，给出你的建议比例，"
    "注意不要超过总预算。",
    "最后帮我把整个发布会的筹备清单整理成一份完整报告，"
    "包含预算明细表、时间线、风险项和待确认事项。",
    "刚才的机动预算如果砍 20%，会影响到哪些项？重新算一遍给我。",
    "把报告里的预算明细再按部门拆分：市场部、供应链、行政各管哪些项。",
]


def run_case_c() -> dict:
    from backend.evaluation.context_budget.driver import chat_stream

    sid = f"p4-caseC-{int(time.time())}"
    results = []
    for i, q in enumerate(CASE_C_SCRIPT):
        r = chat_stream(q, sid, timeout=600)
        results.append(r)
        ctx_events = r.context_events
        print(f"[turn {i + 1}] elapsed={r.elapsed_s:.1f}s ttft="
              f"{r.ttft_s and round(r.ttft_s, 2)}s ctx_events={ctx_events} "
              f"usage={r.done.get('context_usage')} err={r.error}", flush=True)

    all_events = [e for r in results for e in r.context_events]
    by_level: dict[str, list[dict]] = {}
    for e in all_events:
        by_level.setdefault(e.get("level", "?"), []).append(e)

    waterfall = {
        "L1": {"count": len(by_level.get("L1", [])),
               "saved": sum(e.get("saved_tokens", 0) for e in by_level.get("L1", []))},
        "L2": {"count": len(by_level.get("L2", [])),
               "saved": sum(e.get("saved_tokens", 0) for e in by_level.get("L2", []))},
        "L3": {"count": len(by_level.get("L3", [])),
               "saved": sum(e.get("saved_tokens", 0) for e in by_level.get("L3", []))},
        "L4": by_level.get("L4", []),
        "L5": by_level.get("L5", []),
        "final_context_usage": results[-1].done.get("context_usage"),
        "input_budget": 7168,
    }

    # 事实保真抽查：L5 触发后追问历史关键事实
    followups = []
    if by_level.get("L5"):
        probes = [
            ("发布会的总预算是多少？", ["500,000", "50万", "¥500,000"]),
            ("核心产品的 SKU 是什么？", ["POD-X2-GRY-001"]),
            ("官方定价和促销价分别是多少？", ["1,299", "1,099"]),
            ("物料进场验收的截止日期是哪天？", ["2026-10-13"]),
        ]
        for q, musts in probes:
            pr = chat_stream(q, sid, timeout=300)
            ok = all(m in pr.answer for m in musts)
            followups.append({"question": q, "must_contain": musts,
                              "answer_head": pr.answer[:150],
                              "facts_ok": ok,
                              "context_events": pr.context_events})
            print(f"[probe] {q} -> facts_ok={ok}", flush=True)

    report = {
        "mode": "case_c",
        "session_id": sid,
        "turns": [r.summary() for r in results],
        "waterfall": waterfall,
        "fact_probes": followups,
        "fact_retention": (
            sum(1 for f in followups if f["facts_ok"]) / len(followups)
            if followups else None),
    }
    path = _save_report("case_c", report)
    print(json.dumps(waterfall, ensure_ascii=False, indent=2))
    print(f"\nreport: {path}")
    return report


# ── 触发率快照（Prometheus counters）────────────────────────────────────

def run_rate() -> dict:
    import requests as rq
    from backend.evaluation.context_budget.driver import BASE as _B

    # APISIX 代理 /metrics → app；直接打 app 8000 亦可
    for url in ("http://127.0.0.1:8000/metrics", f"{_B}/metrics"):
        try:
            resp = rq.get(url, timeout=10)
            if resp.status_code == 200:
                text = resp.text
                break
        except Exception:
            continue
    else:
        return {"error": "metrics unreachable"}

    snapshot = {}
    for line in text.splitlines():
        if line.startswith("context_compactions_total{"):
            labels = line[line.index("{") + 1:line.index("}")]
            parts = dict(kv.split("=") for kv in labels.split(","))
            level = parts.get("level", "?").strip('"')
            action = parts.get("action", "?").strip('"')
            snapshot[f"{level}:{action}"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("context_tokens_saved_total{"):
            labels = line[line.index("{") + 1:line.index("}")]
            level = dict(kv.split("=") for kv in labels.split(","))["level"].strip('"')
            snapshot[f"saved:{level}"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("chat_requests_total"):
            snapshot["chat_requests_total"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("context_autocompact_llm_tokens_total{"):
            labels = line[line.index("{") + 1:line.index("}")]
            kind = dict(kv.split("=") for kv in labels.split(","))["kind"].strip('"')
            snapshot[f"l5_llm_tokens:{kind}"] = float(line.rsplit(" ", 1)[1])
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return snapshot


MODES = {
    "case_a": run_case_a,
    "case_b": run_case_b,
    "case_c": run_case_c,
    "rate": run_rate,
}


# ── Case C-L5：合法长文档粘贴（真实场景）打满预算，真实触发 L4/L5 ─────
# 依据：Case C 实测正常多轮链路 usage_ratio 上限 ~0.45（L2 历史封顶 2048 +
# po 封顶 1024），L4/L5 在普通聊天下不可达。长文档粘贴是真实业务场景
# （用户贴文档求总结），也是唯一能让单条 prompt 自然逼近预算的方式。

def run_case_c_l5() -> dict:
    from backend.evaluation.context_budget.driver import chat_stream

    sid = f"p4-caseCL5-{int(time.time())}"
    # 阶段 1：铺垫多轮带关键事实的历史（进入 DB，供 L5 增量摘要）
    setup = [
        "我要筹备新品发布会，总预算 ¥500,000，时间 2026-10-15，场地福州会展中心。记一下。",
        "核心产品 EchoPod X2，SKU POD-X2-GRY-001，定价 ¥1,299，促销价 ¥1,099，毛利红线 18%。",
        "物料不要红色主视觉，不要提竞品名字，2026-10-13 前全部进场验收。",
        "媒体投放 100,000 里，小红书 40%、抖音 35%、微信 25%，先按这个分。",
        "伴手礼预算每人 80 元，嘉宾 120 人，由行政部采购。",
        "帮我看看商品数据里有什么",
        "记住了吗？简单复述一下关键数字",
    ]
    for q in setup:
        r = chat_stream(q, sid, timeout=300)
        print(f"[setup] {r.elapsed_s:.0f}s usage={r.done.get('context_usage')}",
              flush=True)

    # 阶段 2：粘贴长文档（真实业务文档内容，~7000 tokens）求总结
    from backend.infra.db import get_memory_engine
    conn = get_memory_engine().raw_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT content FROM rag_vectors WHERE collection='doc_db' "
        "ORDER BY id LIMIT 13")
    doc_text = "\n".join(r[0] for r in cur.fetchall())
    conn.close()

    big_question = (
        "下面是我粘贴的一份运营制度文档，请帮我：1) 总结核心要点成一页纸；"
        "2) 列出 3 个执行风险；3) 说明这份文档里关于金额审批的规定。\n\n"
        "=== 文档开始 ===\n" + doc_text + "\n=== 文档结束 ===")
    r = chat_stream(big_question, sid, timeout=600)
    print(f"[big-paste] elapsed={r.elapsed_s:.0f}s ctx_events={r.context_events}",
          flush=True)
    print(f"[big-paste] usage={r.done.get('context_usage')}", flush=True)

    # 阶段 3：事实保真追问（L5 触发后，历史关键事实是否还在）
    probes = []
    probe_qs = [
        ("发布会的总预算是多少？", ["500,000", "50万", "¥500,000"]),
        ("核心产品的 SKU 是什么？", ["POD-X2-GRY-001"]),
        ("物料验收的截止日期是哪天？", ["2026-10-13"]),
    ]
    for q, musts in probe_qs:
        pr = chat_stream(q, sid, timeout=300)
        ok = all(m in pr.answer for m in musts)
        probes.append({"question": q, "must_contain": musts, "facts_ok": ok,
                       "answer_head": pr.answer[:150],
                       "context_events": pr.context_events})
        print(f"[probe] {q} facts_ok={ok}", flush=True)

    l4 = [e for e in r.context_events if e.get("level") == "L4"]
    l5 = [e for e in r.context_events if e.get("level") == "L5"]
    report = {
        "mode": "case_c_l5",
        "session_id": sid,
        "big_paste": {"elapsed_s": r.elapsed_s,
                      "context_events": r.context_events,
                      "usage": r.done.get("context_usage"),
                      "error": r.error},
        "L4_events": l4,
        "L5_events": l5,
        "fact_probes": probes,
        "fact_retention": (sum(1 for p in probes if p["facts_ok"])
                           / len(probes)) if probes else None,
    }
    path = _save_report("case_c_l5", report)
    print(json.dumps({k: v for k, v in report.items() if k != "fact_probes"},
                     ensure_ascii=False, indent=2))
    print(f"\nreport: {path}")
    return report


MODES["case_c_l5"] = run_case_c_l5


# ── L5 直触发（真实组件级）：app 容器内真实 prepare_llm_context 全链路 ──
# 背景：ChatRequest 2000 字符上限 + L2 历史封顶 2048 使 HTTP 聊天链路
# 结构性到不了 0.80/0.90（实测阴性证据见 case_c/case_c_l5）。本模式在
# app 容器内绑定真实 session 上下文，用真实 DB 历史 + 真实大 prompt 走
# 生产 prepare_llm_context（L2→L4→hard trim→L5→fold_rebuild→SSE sink），
# 除 HTTP 解析层外全部为生产组件与真实数据。

def run_l5_direct() -> dict:
    import asyncio
    import os as _os

    # 进程内加载 DB 模型注册表（app 进程由 startup 刷新循环做，评测进程需手动）
    from backend.infra.llm.registry_store import refresh_registry
    asyncio.run(refresh_registry())

    from backend.context_budget import context_budget as _cb
    from backend.context_budget.metrics import set_context_sink
    from backend.core.request_context import set_session_id
    from langchain_core.messages import HumanMessage, SystemMessage

    import psycopg2
    from backend.infra.db import get_memory_engine

    # 找最近一个 case_c_l5 会话（真实 HTTP 产生的多轮历史）
    conn = get_memory_engine().raw_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT session_id FROM public.chat_sessions "
        "WHERE session_id LIKE 'p4-caseCL5-%' "
        "ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"error": "no case_c_l5 session found; run case_c_l5 first"}
    sid = row[0]
    set_session_id(sid)

    # 真实历史消息（DB 原始行 → LangChain 消息，与 start_session 同构）
    conn = get_memory_engine().raw_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM public.chat_messages "
        "WHERE session_id=%s ORDER BY id", (sid,))
    hist = cur.fetchall()
    cur.execute("SELECT count(*) FROM public.chat_messages WHERE session_id=%s",
                (sid,))
    msg_count_before = cur.fetchone()[0]
    # 大 prompt 内容：真实入库文档 chunk 拼接（~6800 tokens）
    cur.execute(
        "SELECT content FROM rag_vectors WHERE collection='doc_db' "
        "ORDER BY id LIMIT 12")
    doc_text = "\n".join(r[0] for r in cur.fetchall())
    conn.close()

    from backend.memory.token_budget import count_tokens, count_message_tokens
    big_tokens = count_tokens(doc_text)
    messages = [SystemMessage(content="你是电商业务助手")] + [
        HumanMessage(content=c) if r == "user" else
        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(content=c)
        for r, c in hist
    ] + [HumanMessage(
        content=f"以下是粘贴的运营制度文档，请总结要点并列出金额审批规定：\n\n{doc_text}")]
    total_before = sum(count_message_tokens(m) for m in messages)

    events: list[dict] = []
    token = set_context_sink(events.append)
    t0 = time.perf_counter()
    prepared = _cb.prepare_llm_context(messages=messages)
    latency = time.perf_counter() - t0
    from backend.context_budget.metrics import reset_context_sink
    reset_context_sink(token)

    l4 = [e for e in events if e.get("level") == "L4"]
    l5 = [e for e in events if e.get("level") == "L5"]

    # §九 逐项核查
    conn = get_memory_engine().raw_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM public.chat_messages WHERE session_id=%s",
                (sid,))
    msg_count_after = cur.fetchone()[0]
    cur.execute(
        "SELECT summary, summary_through_message_id, summary_token_count, "
        "summary_updated_at, (SELECT max(id) FROM public.chat_messages "
        "WHERE session_id=%s) FROM public.chat_sessions WHERE session_id=%s",
        (sid, sid))
    srow = cur.fetchone()
    conn.close()

    report = {
        "mode": "l5_direct",
        "session_id": sid,
        "raw_context_tokens": total_before,
        "doc_tokens": big_tokens,
        "input_budget": _cb.get_input_budget(),
        "latency_s": round(latency, 2),
        "sse_events": events,
        "L4_events": l4,
        "L5_events": l5,
        "final_usage": prepared.usage.to_dict() if prepared.usage else None,
        "overflow": prepared.overflow,
        "checks": {
            "L4_or_L5_fired": bool(l4 or l5),
            "L5_fired": bool(l5),
            "messages_unchanged": msg_count_before == msg_count_after,
            "summary_written": bool(srow and srow[0]),
            "waterline_set": bool(srow and srow[1]),
            "waterline_is_max_msg_id_or_less": bool(
                srow and srow[1] and srow[1] <= (srow[4] or 0)),
            "summary_token_count": srow[2] if srow else None,
            "summary_updated_at": str(srow[3]) if srow else None,
            "final_within_budget": (prepared.usage.used_tokens
                                    <= _cb.get_input_budget())
            if prepared.usage else False,
        },
        "summary_head": (srow[0][:300] if srow and srow[0] else None),
    }
    path = _save_report("l5_direct", report)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:2500])
    print(f"\nreport: {path}")
    return report


MODES["l5_direct"] = run_l5_direct


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=[*MODES, "golden", "waterline"])
    parser.add_argument("--limit", type=int, default=0, help="golden: 只跑前 N 例")
    parser.add_argument("--category", default="", help="golden: 只跑指定类别")
    parser.add_argument("--smoke", action="store_true",
                        help="golden: smoke 模式（前 5 例，低内存，适合 CI 门禁）；"
                             "不带该参数 = 完整 Golden（发布前执行）")
    args = parser.parse_args()

    if args.mode == "golden":
        from backend.evaluation.context_budget.golden_eval import run_golden
        limit = 5 if args.smoke else args.limit  # --smoke 优先
        report = run_golden(limit=limit, category=args.category)
        path = _save_report("golden" if not args.smoke else "golden_smoke",
                            report)
        print(f"\nreport: {path}")
    elif args.mode == "waterline":
        from backend.evaluation.context_budget.waterline import run_waterline
        report = run_waterline()
        path = _save_report("waterline", report)
        print(f"\nreport: {path}")
    else:
        MODES[args.mode]()


if __name__ == "__main__":
    main()
