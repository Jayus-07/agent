"""run_tool_selector_eval.py — FC 工具选择（tool_selector 节点）离线评测跑批

用法（项目根目录）:
    python backend/evaluation/run_tool_selector_eval.py                # 全量 67 条
    python backend/evaluation/run_tool_selector_eval.py --limit 10     # 冒烟
    python backend/evaluation/run_tool_selector_eval.py --model deepseek-v4-flash

数据集: backend/evaluation/datasets/tool_selector_eval.json
评分维度:
  - 工具选择准确率: 选定 capability == expected.capability
    （no_match 用例: 模型不选中任何"错误工具"即算通过，保守直通可接受）
  - 参数填充准确率: expected.params 精确匹配；params_free_keys 只要求
    存在且非空；params_absent_keys 要求不出现
  - 可用可不用（optional）: 不调工具直接回答，或选中
    expected.acceptable_capabilities 内工具且参数合规，均算通过
  - passthrough 率: source != fc 的比例（降级信号）

输出: data/eval_reports/tool_selector_<ts>.json + 控制台摘要
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DATASET = Path(__file__).parent / "datasets" / "tool_selector_eval.json"
REPORT_DIR = ROOT / "data" / "eval_reports"


def load_dataset(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["test_cases"]


def grade_params(expected: dict, actual: dict | None) -> tuple[bool, list[str]]:
    """参数评分。返回 (ok, 失败明细)。actual=None（passthrough）视为缺参。"""
    problems: list[str] = []
    actual = actual or {}

    for key, want in (expected.get("params") or {}).items():
        got = actual.get(key)
        if got != want:
            problems.append(f"{key}: 期望 {want!r} 实际 {got!r}")

    for key in expected.get("params_free_keys") or []:
        val = actual.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            problems.append(f"{key}: 自由文本参数缺失/为空")

    for key in expected.get("params_absent_keys") or []:
        if key in actual and actual[key] not in (None, "", {}, []):
            problems.append(f"{key}: 不应填参却填了 {actual[key]!r}（编造）")

    return (not problems), problems


def run_case(case: dict) -> dict:
    from backend.orchestration.graph.tool_selector import tool_selector_node

    state = {
        "question": case["query"],
        "session_id": f"eval-{case['id']}",
        "route_mode": "direct",
        "route_decision": {
            "execution_mode": "direct",
            "candidates": case["candidates"],
            "confidence": case["candidates"][0].get("score", 0.7),
        },
    }
    t0 = time.time()
    try:
        out = tool_selector_node(state)
    except Exception as e:
        return {"id": case["id"], "error": str(e)[:200], "elapsed_s": round(time.time() - t0, 2)}
    sel = out.get("_tool_selection") or {}
    resolved = out.get("resolved_params")
    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "source": sel.get("source"),
        "reason": sel.get("reason"),
        "selected": sel.get("capability") or out.get("route_decision", {}).get("candidates", [{}])[0].get("name"),
        "resolved_params": resolved,
        "attempts": sel.get("attempts"),
        "elapsed_s": round(time.time() - t0, 2),
    }


def grade(result: dict, case: dict) -> dict:
    """对单条结果打分，附加 select_ok / params_ok / no_match_ok。"""
    expected = case["expected"]
    result["select_ok"] = False
    result["params_ok"] = None
    result["no_match_ok"] = None

    if expected.get("acceptable_capabilities"):
        # 可用可不用用例：模型凭自身知识直接回答（不调工具，保守直通/
        # 无匹配均算过），或选中可接受能力内且参数合规。参数按选中能力
        # 的 params_by_cap 校验（不同可接受能力的参数期望不同）。
        result["optional_ok"] = (
            result.get("source") != "fc"
            or result.get("selected") in expected["acceptable_capabilities"]
        )
        result["select_ok"] = bool(result["optional_ok"])
        if result["select_ok"] and result.get("source") == "fc":
            cap_expected = (expected.get("params_by_cap") or {}).get(
                result["selected"], {})
            ok, problems = grade_params(cap_expected, result.get("resolved_params"))
            result["params_ok"] = ok
            result["param_problems"] = problems
        return result

    if expected.get("no_match"):
        # 无匹配用例：模型没选中"真实意图错误"的工具即可
        # （no_match 保守直通与明确无匹配都算过；fc 选中即错）
        result["no_match_ok"] = result.get("source") != "fc"
        result["select_ok"] = bool(result["no_match_ok"])
        return result

    want_cap = expected.get("capability")
    result["select_ok"] = result.get("selected") == want_cap and result.get("source") == "fc"
    if result["select_ok"]:
        ok, problems = grade_params(expected, result.get("resolved_params"))
        result["params_ok"] = ok
        result["param_problems"] = problems
    return result


def summarize(results: list[dict]) -> dict:
    graded = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    fc_rows = [r for r in graded if r.get("source") == "fc"]
    no_match_rows = [r for r in graded if r.get("category") == "no_match"]
    cap_rows = [r for r in graded if r.get("category") != "no_match"]

    by_category: dict[str, dict] = {}
    for r in graded:
        stat = by_category.setdefault(r["category"], {"total": 0, "select_ok": 0, "params_ok": 0})
        stat["total"] += 1
        stat["select_ok"] += int(bool(r.get("select_ok")))
        if r.get("params_ok") is True:
            stat["params_ok"] += 1

    return {
        "total": len(results),
        "errors": len(errors),
        "fc_rate": round(len(fc_rows) / len(graded), 3) if graded else 0,
        "passthrough_rate": round(
            sum(1 for r in graded if r.get("source") == "passthrough") / len(graded), 3)
        if graded else 0,
        "select_accuracy": round(
            sum(1 for r in cap_rows if r.get("select_ok")) / len(cap_rows), 3)
        if cap_rows else 0,
        "params_accuracy": round(
            sum(1 for r in fc_rows if r.get("params_ok") is True) / len(fc_rows), 3)
        if fc_rows else 0,
        "no_match_accuracy": round(
            sum(1 for r in no_match_rows if r.get("no_match_ok")) / len(no_match_rows), 3)
        if no_match_rows else 0,
        "by_category": by_category,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FC 工具选择评测")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    parser.add_argument("--model", type=str, default="",
                        help="覆盖 TOOL_SELECTOR_MODEL（如 deepseek-v4-flash）")
    args = parser.parse_args()

    if args.model:
        import backend.orchestration.graph.tool_selector as ts
        ts.TOOL_SELECTOR_MODEL = args.model
        print(f"[eval] 使用专用模型: {args.model}")

    cases = load_dataset(args.dataset)
    if args.limit:
        cases = cases[:args.limit]
    print(f"[eval] 用例数: {len(cases)}")

    results = []
    for i, case in enumerate(cases, 1):
        result = run_case(case)
        result = grade(result, case)
        results.append(result)
        flag = "✓" if result.get("select_ok") else "✗"
        print(f"[{i:>3}/{len(cases)}] {flag} {case['id']} ({case['category']}) "
              f"source={result.get('source')} selected={result.get('selected')} "
              f"{result.get('elapsed_s', 0)}s")

    summary = summarize(results)
    report = {"dataset": str(args.dataset), "summary": summary, "results": results}

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"tool_selector_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 摘要 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n报告: {out_path}")

    failures = [r for r in results
                if not r.get("select_ok") or r.get("params_ok") is False]
    if failures:
        print(f"\n失败用例 {len(failures)} 条（回填示例库/prompt 的 badcase 来源）:")
        for r in failures:
            print(f"  {r['id']}: selected={r.get('selected')} "
                  f"params_ok={r.get('params_ok')} problems={r.get('param_problems')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
