"""run_tool_routing_eval.py — 工具路由评测（2026-09-22 D6 修复验收）

用法（项目根目录；需 embedding 供应商已绑定 + pgvector 可达）:
    python backend/evaluation/run_tool_routing_eval.py            # 全量
    python backend/evaluation/run_tool_routing_eval.py --limit 5  # 冒烟

数据集: backend/evaluation/datasets/tool_routing_eval.json
输出列: query | expected_tool | top1 | top1_score | top2 | decision_path | result
验收门槛:
  - SQL 明确场景（strict=true）Top1 命中率 >= 95%
  - 采集类场景不得误路由到 sql.query（forbid 命中即失败）
  - 其他域用例（RAG/旅游）不得被校准外溢影响

输出: data/eval_reports/tool_routing_<ts>.json + 控制台表格
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DATASET = Path(__file__).parent / "datasets" / "tool_routing_eval.json"
REPORT_DIR = ROOT / "data" / "eval_reports"
SQL_GATE = 0.95  # SQL 明确场景 Top1 命中率门槛


def _decision_path(meta: dict | None) -> str:
    """decision_path：粗分类动作 → 细路由方式（含 rule override）。"""
    if not meta:
        return "legacy"
    action = meta.get("domain_action") or ""
    if action == "tool_route":
        calib = (meta.get("calibration") or {}).get("basis", "")
        suffix = f"/{calib}" if calib else ""
        return f"{meta.get('domain', '?')}/tool_route/{meta.get('tool_route_mode', '?')}{suffix}"
    return f"{meta.get('domain', '?') or '?'}/{action or meta.get('architecture', '?')}"


def _bootstrap_llm_registry() -> None:
    """独立进程运行时先拉一次 DB 配置层（provider/credentials/专项绑定）。

    常驻 app 由启动流程注入；评测/脚本进程缺这步会导致 embedding 绑定
    解析为空（VectorRouter 初始化失败 → 全部降级 LLM Router，评测失真）。
    失败软处理：保持 env 兜底语义。
    """
    try:
        import asyncio

        from backend.infra.llm.registry_store import refresh_registry

        asyncio.run(refresh_registry())
    except Exception as e:
        print(f"[bootstrap] refresh_registry 失败（env 兜底）: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    args = parser.parse_args()

    _bootstrap_llm_registry()

    from backend.orchestration.router.hierarchical import get_hierarchical_router

    router = get_hierarchical_router()
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cases = cases[: args.limit]

    rows: list[dict] = []
    for case in cases:
        query = case["query"]
        expected = case.get("expected_tools") or []
        strict = case.get("strict", False)
        forbid = set(case.get("forbid") or [])

        decision = router.route(query)
        meta = decision.routing_meta or {}
        cands = decision.candidates or []
        top1 = cands[0].name if cands else (meta.get("fine_top1") or "")
        top1_score = cands[0].score if cands else float(meta.get("fine_top1_score") or 0.0)

        # top2 统一口径：优先 routing_meta.candidate_tools 第二位（域内排序），
        # 否则候选列表第二位
        ct = meta.get("candidate_tools") or []
        top2 = ct[1] if len(ct) > 1 else (cands[1].name if len(cands) > 1 else "")

        # ── 判定 ──────────────────────────────────────────────
        if top1 in forbid:
            result = "FAIL(误入禁用工具)"
        elif strict:
            result = "PASS" if top1 == expected[0] else "FAIL(top1不符)"
        elif expected:
            result = "PASS" if top1 in expected else "FAIL(top1不符)"
        else:
            # expected 为空（域图类域）：只要未命中禁用工具即过
            result = "PASS(不串域)"

        rows.append({
            "id": case["id"],
            "query": query,
            "expected_tool": "/".join(expected) or "-",
            "top1": top1 or "-",
            "top1_score": round(top1_score, 3),
            "top2": top2 or "-",
            "decision_path": _decision_path(meta),
            "result": result,
            "strict": strict,
        })

    # ── 汇总 ──────────────────────────────────────────────────
    sql_rows = [r for r in rows if r["strict"]]
    sql_hit = sum(1 for r in sql_rows if r["result"] == "PASS")
    sql_rate = sql_hit / len(sql_rows) if sql_rows else 0.0
    fail_rows = [r for r in rows if r["result"].startswith("FAIL")]

    print(f"{'query':<28} | {'expected':<24} | {'top1':<20} | "
          f"{'score':>5} | {'top2':<16} | decision_path | result")
    print("-" * 150)
    for r in rows:
        print(f"{r['query']:<28} | {r['expected_tool']:<24} | {r['top1']:<20} | "
              f"{r['top1_score']:>5.3f} | {r['top2']:<16} | {r['decision_path']} | {r['result']}")

    summary = {
        "total": len(rows),
        "sql_strict_total": len(sql_rows),
        "sql_strict_hit": sql_hit,
        "sql_top1_accuracy": round(sql_rate, 4),
        "sql_gate_pass": sql_rate >= SQL_GATE,
        "failed_cases": [r["id"] for r in fail_rows],
    }
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"tool_routing_{int(time.time())}.json"
    report_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"report: {report_path}")
    return 0 if summary["sql_gate_pass"] and not fail_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
