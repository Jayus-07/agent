"""e2e_travel_quality.py — STOP I 真实 Gateway 质量 E2E 驱动（Q-E2E-1~6）。

任务书 §77：STOP I 不重复 STOP H Infra 验收，但必须经真实链路
（APISIX → JWT → FastAPI → Router → Travel Graph → SSE）确认新 planner
与质量契约在真实运行时接线。

六个场景（每个场景独立会话，含自动断言）：
  Q-E2E-1 basic itinerary     厦门三天 → 3 天行程 + 数据来源披露
  Q-E2E-2 pending→continuation 缺槽追问 → 补「三天」→ 行程与单轮质量一致
  Q-E2E-3 avoid PATCH         completed 态「不去鼓浪屿了」→ 走 STOP I2 新
                              路由通道（0 信号词被 resolver 接住）→ 新单不含鼓浪屿
  Q-E2E-4 must-go             必去鼓浪屿 → 行程含鼓浪屿
  Q-E2E-5 budget change       「预算改成5000」（裸数字）→ 重排且回显预算
  Q-E2E-6 NEW_RUN             「不去厦门了，重新规划杭州两天」→ 杭州新单，旧目的地零泄漏

用法（复用 STOP H 账号与基础设施）：
    cd backend
    python scripts/e2e_travel_runtime.py --setup --password <pwd>   # 一次
    python scripts/e2e_travel_quality.py --password <pwd>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e_travel_runtime import (  # noqa: E402
    BASE,
    PATH_PREFIX,
    chat_stream,
    jwt_claims,
    login,
)


def sse_shapes_ok(result: dict) -> tuple[bool, str]:
    """SSE 契约：200 + event-stream + meta 首帧 + done 恰一次。"""
    if result.get("status") != 200:
        return False, f"status={result.get('status')} body={result.get('error_body')}"
    if "text/event-stream" not in (result.get("content_type") or ""):
        return False, f"content_type={result.get('content_type')}"
    events = result.get("events") or []
    if not events:
        return False, "no events"
    first = (events[0] or {}).get("event")
    if first not in ("meta", None):
        return False, f"first frame={first}"
    done = sum(1 for e in events if e.get("event") == "done"
               or (isinstance(e.get("data"), dict)
                   and e["data"].get("type") in ("done", "end")))
    if done != 1:
        return False, f"done_count={done}"
    return True, ""


class Scenario:
    def __init__(self, sid: str, turns: list[tuple[str, list[tuple[str, str]]]],
                 conversation: str):
        self.sid = sid
        self.turns = turns          # (question, [(needle, label) 必含], …) 见下
        self.conversation = conversation


def run_scenario(sid: str, token: str, user: str, steps: list[dict]) -> dict:
    """顺序执行一轮会话内的多步对话，逐步断言。

    steps: [{"q": 问题, "must": [必含], "must_not": [必不含], "meta_first": bool}]
    """
    conv = f"stopI-{sid}-{int(time.time())}"
    failures: list[str] = []
    print(f"\n===== [{sid}] conv={conv}")
    for i, step in enumerate(steps, start=1):
        q = step["q"]
        print(f"  [turn{i}] U: {q}")
        result = chat_stream(token, conv, q, timeout=180)
        ok, why = sse_shapes_ok(result)
        if not ok:
            failures.append(f"turn{i} SSE: {why}")
            print(f"    [FAIL] {why}")
            continue
        answer = result.get("answer") or ""
        print(f"    [sse] frames={len(result.get('events', []))} "
              f"latency={result.get('latency_ms', 0):.0f}ms answer_len={len(answer)}")
        print(f"    [answer] {answer[:160].replace(chr(10), ' | ')}")
        for needle in step.get("must", []):
            if needle not in answer:
                failures.append(f"turn{i} 答案缺「{needle}」")
                print(f"    [FAIL] 缺「{needle}」")
        for needle in step.get("must_not", []):
            if needle in answer:
                failures.append(f"turn{i} 答案不应含「{needle}」")
                print(f"    [FAIL] 不应含「{needle}」")
    return {"sid": sid, "conversation": conv, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="e2e_travel")
    parser.add_argument("--base", default="")
    args = parser.parse_args()

    if args.base:
        os.environ["E2E_BASE"] = args.base

    token = login(args.username, args.password)
    claims = jwt_claims(token)
    user = str(claims.get("userId") or claims.get("user_id") or "")
    print(f"[jwt] claims={sorted(claims)} base={BASE}{PATH_PREFIX or ''}")

    results: list[dict] = []

    # ── Q-E2E-1 基础行程 ──
    results.append(run_scenario(
        "q_e2e_1_basic", token, user, [
            {"q": "帮我规划厦门3天的行程",
             "must": ["厦门", "3 天行程", "数据来源", "本地示例数据"]},
        ]))

    # ── Q-E2E-2 缺槽追问 → 补槽续跑（质量与单轮一致）──
    results.append(run_scenario(
        "q_e2e_2_pending", token, user, [
            {"q": "帮我规划厦门的行程", "must": ["几天"]},
            {"q": "三天", "must": ["厦门", "3 天行程"]},
        ]))

    # ── Q-E2E-3 avoid PATCH（completed 态，走 STOP I2 新路由通道）──
    results.append(run_scenario(
        "q_e2e_3_avoid_patch", token, user, [
            {"q": "帮我规划厦门2天的行程", "must": ["厦门", "2 天行程"]},
            # 0 旅游信号词 0 城市名：只有 STOP I2 的 avoid 通道能接住
            {"q": "不去鼓浪屿了", "must": ["厦门"], "must_not": ["鼓浪屿"]},
        ]))

    # ── Q-E2E-4 must-go ──
    results.append(run_scenario(
        "q_e2e_4_must_go", token, user, [
            {"q": "帮我规划厦门2天的行程，必去鼓浪屿",
             "must": ["鼓浪屿"]},
        ]))

    # ── Q-E2E-5 预算 PATCH（裸数字抽取）──
    results.append(run_scenario(
        "q_e2e_5_budget", token, user, [
            {"q": "帮我规划福州2天的行程，预算3000元", "must": ["福州"]},
            {"q": "预算改成5000", "must": ["预算 ¥5000"]},
        ]))

    # ── Q-E2E-6 NEW_RUN 目的地变更（旧目的地零泄漏）──
    results.append(run_scenario(
        "q_e2e_6_new_run", token, user, [
            {"q": "帮我规划厦门2天的行程", "must": ["厦门"]},
            {"q": "不去厦门了，重新规划杭州两天的行程",
             "must": ["杭州", "2 天行程"],
             "must_not": ["鼓浪屿", "厦门大学", "南普陀寺"]},
        ]))

    print("\n===== [STOP I 质量 E2E 汇总]")
    all_ok = True
    for r in results:
        status = "PASS" if not r["failures"] else "FAIL"
        all_ok &= not r["failures"]
        print(f"  {r['sid']}: {status}"
              + ("" if not r["failures"] else f"  —— {r['failures']}"))
    print(f"\nSTOP_I_QUALITY_E2E_{'PASS' if all_ok else 'FAIL'}=true")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
