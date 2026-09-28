"""production_smoke — 生产上线前最小 Release Smoke（STOP F，production_smoke_v1）

对真实运行栈（APISIX :9080 经管理端代理）按真实 HTTP 执行 33 个 case：
  - rag_retrieval：/api/rag/search 纯检索（含跨知识库可见性探测）
  - rag_qa：/api/rag/ask 端到端问答（引用 + 关键事实 + 拒答）
  - chat_route：/api/chat 行为级路由（RAG/SQL/旅游/客服/通用/多意图/拒答）

Gate（F4）：任何基础设施错误（HTTP 5xx / 连接失败 / 「表不存在」/
「RAG 服务调用失败」等）→ 整体 FAIL；业务质量指标如实输出不设人为阈值。

用法（仓库根）：
    python backend/scripts_e2e/production_smoke.py --base http://localhost:3200
输出：docs/evidence/production-smoke/smoke-<ts>.json + .md；exit 0/1。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BASE = "http://localhost:3200"
USERNAME = os_environ_user = "admin"
PASSWORD = "admin"
INFRA_PATTERNS = [
    "relation \".*\" does not exist", "表不存在", "RAG 服务调用失败",
    "Prompt 加载失败", "Internal Server Error", "服务器内部错误",
]
REFUSAL_CUES = ["没有找到相关", "知识库暂无", "无法找到", "未找到相关",
                "没有相关资料", "无法回答", "抱歉，暂时", "知识库中没有"]


def _post(path: str, body: dict, token: str, timeout: int = 120) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")[:400]
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def login() -> str:
    req = urllib.request.Request(
        "http://localhost:9080/api/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["data"]["token"]


def _is_infra_error(status: int, body) -> bool:
    if status >= 500 or status == -1:
        return True
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return any(re.search(p, text) for p in INFRA_PATTERNS)


def run_retrieval_case(token: str, case: dict) -> dict:
    t0 = time.monotonic()
    status, body = _post("/api/rag/search",
                         {"query": case["q"], "kb_id": case["kb"], "top_k": 3},
                         token, timeout=90)
    dt = (time.monotonic() - t0) * 1000
    out = {"id": case["id"], "suite": "rag_retrieval", "latency_ms": round(dt),
           "status_http": status}
    if _is_infra_error(status, body):
        out.update(passed=False, infra_error=True, reason=f"infra: {str(body)[:120]}")
        return out
    results = body.get("results", []) if isinstance(body, dict) else []
    top_join = " ".join(r.get("content", "") for r in results[:3])
    hit = any(re.search(p, top_join) for p in case["must"]) if case["must"] else bool(results)
    top1_preview = results[0].get("content", "")[:60] if results else ""
    out.update(passed=hit and bool(results),
               no_result=not results,
               reason="" if hit else f"top3 未命中 {case['must']}，top1={top1_preview!r}")
    return out


def run_rag_qa_case(token: str, case: dict) -> dict:
    t0 = time.monotonic()
    status, body = _post("/api/rag/ask",
                         {"question": case["q"], "kb_id": case.get("kb", "policy_general")},
                         token, timeout=180)
    dt = (time.monotonic() - t0) * 1000
    out = {"id": case["id"], "suite": "rag_qa", "latency_ms": round(dt),
           "status_http": status}
    if _is_infra_error(status, body):
        out.update(passed=False, infra_error=True, reason=f"infra: {str(body)[:120]}")
        return out
    answer = body.get("answer", "") if isinstance(body, dict) else str(body)
    sources = body.get("sources", []) if isinstance(body, dict) else []
    is_refusal_case = case.get("expect") == "refusal"
    refused = any(c in answer for c in REFUSAL_CUES)
    cited = bool(sources) or bool(re.search(r"\[E\d", answer))
    if is_refusal_case:
        out.update(passed=refused, refused=refused, cited=cited,
                   reason="" if refused else f"应拒答未拒答：{answer[:80]!r}")
    else:
        must_ok = (not case["must"]) or any(re.search(p, answer) for p in case["must"])
        out.update(passed=bool(answer) and must_ok and not refused,
                   refused=refused, cited=cited,
                   reason="" if (must_ok and not refused) else f"answer={answer[:80]!r}")
    if case.get("critical"):
        out["critical"] = True
        out["critical_ok"] = bool(re.search(case["critical"], answer))
    return out


def run_chat_case(token: str, case: dict) -> dict:
    t0 = time.monotonic()
    status, body = _post("/api/chat",
                         {"question": case["q"],
                          "session_id": f"smoke-{uuid.uuid4().hex[:8]}"},
                         token, timeout=240)
    # 网关 504（LLM 慢导致超过 APISIX 60s 代理超时）属瞬态：重试一次。
    # LLM 供应商排队抖动不应判成基础设施永久故障；连续两次仍失败才计 infra。
    if status == 504:
        status, body = _post("/api/chat",
                             {"question": case["q"],
                              "session_id": f"smoke-{uuid.uuid4().hex[:8]}"},
                             token, timeout=240)
    dt = (time.monotonic() - t0) * 1000
    out = {"id": case["id"], "suite": "chat_route", "latency_ms": round(dt),
           "status_http": status, "expect_route": case.get("route", "")}
    if _is_infra_error(status, body):
        out.update(passed=False, infra_error=True, reason=f"infra: {str(body)[:120]}")
        return out
    answer = body.get("answer", "") if isinstance(body, dict) else str(body)
    refused = any(c in answer for c in REFUSAL_CUES)
    if case.get("expect") == "refusal_or_general":
        out.update(passed=bool(answer), refused=refused,
                   reason="" if answer else "空回答")
    elif case.get("expect") == "answer_or_clarify":
        # 多意图场景：路由到任一域并给出连贯回答 / 追问澄清均为设计内行为
        out.update(passed=bool(answer), refused=refused,
                   reason="" if answer else "空回答")
    else:
        must_ok = (not case["must"]) or any(re.search(p, answer) for p in case["must"])
        out.update(passed=bool(answer) and must_ok,
                   refused=refused,
                   reason="" if must_ok else f"answer={answer[:80]!r}")
    return out


CASES_RETRIEVAL = [
    {"id": "RT-01", "q": "员工差旅报销的上限金额是多少", "kb": "policy_general", "must": ["差旅", "报销"]},
    {"id": "RT-02", "q": "会议室预订最长时长", "kb": "policy_general", "must": ["会议室", "预订", "小时"]},
    {"id": "RT-03", "q": "差旅住宿报销标准", "kb": "policy_general", "must": ["住宿", "报销"]},
    {"id": "RT-04", "q": "报销单据需要在多少个工作日内提交", "kb": "policy_general", "must": ["工作日", "提交", "报销"]},
    {"id": "RT-05", "q": "报销审批要经过哪些环节", "kb": "policy_general", "must": ["审批", "负责人", "财务"]},
    {"id": "RT-06", "q": "生产收口冒烟文档的住宿上限", "kb": "policy_general", "must": ["住宿", "报销"]},
    {"id": "RT-07", "q": "供应商准入需要提交哪些材料", "kb": "rag_100_docs", "must": ["供应商", "材料", "准入"]},
    {"id": "RT-08", "q": "信息安全管理制度适用范围", "kb": "rag_100_docs", "must": ["信息安全", "制度"]},
    {"id": "RT-09", "q": "招聘流程有哪些环节", "kb": "rag_100_docs", "must": ["招聘"]},
    {"id": "RT-10", "q": "印章使用需要什么审批", "kb": "rag_100_docs", "must": ["印章"]},
    {"id": "RT-11", "q": "考勤管理制度对迟到怎么规定", "kb": "rag_100_docs", "must": ["考勤"]},
    {"id": "RT-12", "q": "知识产权转让协议的关键条款", "kb": "rag_100_docs", "must": ["知识产权", "转让"]},
]

CASES_QA = [
    {"id": "QA-01", "q": "员工差旅报销的上限金额是多少？", "expect": "answer",
     "must": ["3,888", "3888"], "critical": r"3,888|3888"},
    {"id": "QA-02", "q": "差旅住宿每晚的报销上限是多少？", "expect": "answer", "must": ["元"]},
    {"id": "QA-03", "q": "会议室预订的最长时长是多久？", "expect": "answer", "must": ["小时", "4"]},
    {"id": "QA-04", "q": "报销单的审批流程是怎样的？", "expect": "answer", "must": ["审批", "财务|负责人"]},
    {"id": "QA-05", "q": "量子计算机的保修政策是什么？", "expect": "refusal"},
    {"id": "QA-06", "q": "公司对区块链挖矿业务有哪些福利制度？", "expect": "refusal"},
]

CASES_CHAT = [
    {"id": "CH-01", "q": "根据公司制度，员工差旅报销的上限金额是多少？", "route": "rag",
     "must": ["3,888|3888"]},
    {"id": "CH-02", "q": "员工差旅报销需要经过哪些审批环节？", "route": "rag",
     "must": ["审批|负责人|财务"]},
    {"id": "CH-03", "q": "请用一句话介绍你自己", "route": "general", "must": []},
    {"id": "CH-04", "q": "你好", "route": "general", "must": []},
    {"id": "CH-05", "q": "技术部有多少人？", "route": "sql", "must": []},
    {"id": "CH-06", "q": "福州有哪些值得去的景点", "route": "travel", "must": []},
    {"id": "CH-07", "q": "你们的退货政策是什么", "route": "cs", "must": []},
    {"id": "CH-08", "q": "宇宙飞船的驾照应该怎么考？", "route": "general", "expect": "refusal_or_general"},
    {"id": "CH-09", "q": "我下周要去福州出差，想了解公司差旅报销的标准，另外帮我看看福州有哪些景点",
     "route": "rag+travel", "expect": "answer_or_clarify", "must": []},
    {"id": "CH-10", "q": "再问一次：员工差旅报销的上限金额是多少？", "route": "rag",
     "must": ["3,888|3888"]},
]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return round(s[k], 1)


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--out", default="docs/evidence/production-smoke")
    args = ap.parse_args()
    BASE = args.base

    token = login()
    print("登录成功，开始生产冒烟：33 case（检索 12 / 问答 6 / 聊天路由 10 … 实际以表为准）")
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(run_retrieval_case, token, c) for c in CASES_RETRIEVAL]
        for f in futs:
            results.append(f.result())
    print(f"rag_retrieval 完成 {len(CASES_RETRIEVAL)}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(run_rag_qa_case, token, c) for c in CASES_QA]
        for f in futs:
            results.append(f.result())
    print(f"rag_qa 完成 {len(CASES_QA)}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(run_chat_case, token, c) for c in CASES_CHAT]
        for f in futs:
            results.append(f.result())
    print(f"chat_route 完成 {len(CASES_CHAT)}")

    # ── 汇总 ──
    total = len(results)
    infra_errors = [r for r in results if r.get("infra_error")]
    passed = [r for r in results if r["passed"]]
    latencies = [r["latency_ms"] for r in results]
    qa = [r for r in results if r["suite"] == "rag_qa"]
    qa_normal = [r for r in qa if not r.get("refused") and r.get("cited") is not None]
    refusals = [r for r in results if r["id"] in ("QA-05", "QA-06")]
    criticals = [r for r in results if r.get("critical")]
    chat = [r for r in results if r["suite"] == "chat_route"]

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "total_cases": total,
        "runtime_success_rate": round(len(passed) / total, 4),
        "infra_error_count": len(infra_errors),
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "refusal_correctness": round(
            sum(1 for r in refusals if r["passed"]) / len(refusals), 4) if refusals else None,
        "citation_present_rate": round(
            sum(1 for r in qa_normal if r.get("cited")) / len(qa_normal), 4) if qa_normal else None,
        "critical_fact_accuracy": round(
            sum(1 for r in criticals if r.get("critical_ok")) / len(criticals), 4) if criticals else None,
        "retrieval_success_rate": round(
            sum(1 for r in results if r["suite"] == "rag_retrieval" and r["passed"])
            / len(CASES_RETRIEVAL), 4),
        "chat_route_success_rate": round(
            sum(1 for r in chat if r["passed"]) / len(chat), 4),
        "gate": {"infra_errors_must_be_zero": len(infra_errors) == 0},
    }
    summary["PRODUCTION_SMOKE_EVAL_PASS"] = (
        summary["gate"]["infra_errors_must_be_zero"])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"smoke-{ts}.json").write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    lines = ["# Production Smoke（production_smoke_v1）", "",
             f"- 时间：{summary['timestamp']} ｜ case 数：{total}",
             f"- runtime_success_rate：**{summary['runtime_success_rate']:.1%}**",
             f"- infra_error_count：**{summary['infra_error_count']}**（gate：必须为 0）",
             f"- P50/P95：{summary['p50_latency_ms']}ms / {summary['p95_latency_ms']}ms",
             f"- 拒答正确率：{summary['refusal_correctness']} ｜ 引用出现率：{summary['citation_present_rate']}",
             f"- 关键事实准确率：{summary['critical_fact_accuracy']}",
             f"- 检索成功：{summary['retrieval_success_rate']} ｜ 聊天路由（行为级）：{summary['chat_route_success_rate']}",
             f"- **PRODUCTION_SMOKE_EVAL_PASS = {summary['PRODUCTION_SMOKE_EVAL_PASS']}**",
             "", "| case | 套件 | 结果 | 耗时ms | 说明 |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['suite']} | {'✅' if r['passed'] else '❌'}"
                     f"{'⚠️infra' if r.get('infra_error') else ''} | {r['latency_ms']} | {r.get('reason','')[:80]} |")
    (out_dir / f"smoke-{ts}.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failed = [r for r in results if not r["passed"]]
    for r in failed:
        print(f"FAIL {r['id']} [{r['suite']}] {r.get('reason','')[:100]}")
    return 0 if summary["PRODUCTION_SMOKE_EVAL_PASS"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
