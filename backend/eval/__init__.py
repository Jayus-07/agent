"""
eval 框架 — RAG 评测（2026-08-11 P1 Golden Dataset）

使用:
    python -m scripts.weekly_eval
    # 或
    from backend.eval import run_golden_eval
    run_golden_eval("eval/golden_v1.json")
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent
DEFAULT_DATASET = GOLDEN_DIR / "golden_v1.json"
HISTORY_DB = Path("data/eval_history.db")


def load_golden(path: str | Path = DEFAULT_DATASET) -> list[dict]:
    """加载 Golden Dataset。"""
    with open(path, "r", encoding="utf-8") as f:
        cases = json.load(f)
    if not isinstance(cases, list):
        raise ValueError(f"golden dataset 必须是 list，得到: {type(cases)}")
    return cases


def evaluate_response(
    response: dict[str, Any],
    case: dict[str, Any],
    latency_ms: int,
) -> dict[str, Any]:
    """评估单个 case 的 RAG 响应。

    评分维度：
      - hit: 是否有 RAG 输出（status='ok'）
      - rejected: RAG 被拒答（status='rejected'）
      - top_doc_hit: 命中的文档是否在 expected_docs 列表里
      - domain_match: 命中文档的业务域是否匹配 expected_domains
      - top1_score: Rerank top1 分数（如果有）
      - pass: hit=True AND top_doc_hit=True
    """
    answer = (response.get("answer") or "").strip()
    sources = response.get("sources") or []
    rejected = "无答案" in answer or "无相关" in answer or "资料未提及" in answer
    hit = bool(answer) and answer != "[ERROR]" and not rejected

    actual_doc_ids = {s.get("doc_id") for s in sources if s.get("doc_id")}
    expected_doc_ids = set(case.get("expected_docs") or [])
    top_doc_hit = bool(expected_doc_ids & actual_doc_ids)

    actual_domains = {s.get("metadata", {}).get("business_domain") for s in sources}
    expected_domains = set(case.get("expected_domains") or [])
    domain_match = bool(expected_domains & actual_domains) if expected_domains else None

    top1_score = (
        sources[0].get("metadata", {}).get("rerank_score") if sources else None
    )

    passed = hit and not rejected and (top_doc_hit or not expected_doc_ids)

    return {
        "case_id": case.get("id"),
        "query": case.get("query"),
        "hit": hit,
        "rejected": rejected,
        "top_doc_hit": top_doc_hit,
        "domain_match": domain_match,
        "top1_score": top1_score,
        "latency_ms": latency_ms,
        "passed": passed,
        "actual_doc_ids": list(actual_doc_ids),
        "expected_doc_ids": list(expected_doc_ids),
        "answer_preview": answer[:100],
    }


def aggregate(result: dict[str, Any]) -> dict[str, Any]:
    """汇总评测结果。"""
    total = len(result["results"])
    hit = sum(1 for r in result["results"] if r["hit"])
    rejected = sum(1 for r in result["results"] if r["rejected"])
    top_doc_hits = sum(1 for r in result["results"] if r.get("top_doc_hit"))
    passed = sum(1 for r in result["results"] if r["passed"])
    latencies = [r["latency_ms"] for r in result["results"] if r["latency_ms"]]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    return {
        "total": total,
        "hit_rate": hit / total if total > 0 else 0,
        "reject_rate": rejected / total if total > 0 else 0,
        "top_doc_hit_rate": top_doc_hits / total if total > 0 else 0,
        "pass_rate": passed / total if total > 0 else 0,
        "avg_latency_ms": int(avg_latency),
        "details": result["results"],
    }


def save_history(summary: dict[str, Any], dataset: str = "golden_v1") -> None:
    """保存评测历史到 SQLite（用于趋势图）。"""
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(HISTORY_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT DEFAULT (datetime('now')),
                dataset TEXT,
                total INTEGER,
                hit_rate REAL,
                reject_rate REAL,
                top_doc_hit_rate REAL,
                pass_rate REAL,
                avg_latency_ms INTEGER
            )
        """
        )
        # 取 details 之外的所有字段作总览
        overview = {k: v for k, v in summary.items() if k != "details"}
        conn.execute(
            """INSERT INTO eval_history
            (dataset, total, hit_rate, reject_rate, top_doc_hit_rate, pass_rate, avg_latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset,
                overview["total"],
                overview["hit_rate"],
                overview["reject_rate"],
                overview["top_doc_hit_rate"],
                overview["pass_rate"],
                overview["avg_latency_ms"],
            ),
        )
        # 详情写 detail 表
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT DEFAULT (datetime('now')),
                case_id TEXT,
                query TEXT,
                hit INTEGER, rejected INTEGER, top_doc_hit INTEGER,
                domain_match INTEGER, top1_score REAL, latency_ms INTEGER,
                passed INTEGER, answer_preview TEXT
            )
        """
        )
        for r in summary["details"]:
            conn.execute(
                """INSERT INTO eval_details
                (case_id, query, hit, rejected, top_doc_hit, domain_match,
                 top1_score, latency_ms, passed, answer_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r["case_id"], r["query"],
                    int(r["hit"]), int(r["rejected"]),
                    int(r["top_doc_hit"]),
                    int(r["domain_match"]) if r["domain_match"] is not None else None,
                    r["top1_score"],
                    r["latency_ms"],
                    int(r["passed"]),
                    r["answer_preview"],
                ),
            )


def history(days: int = 30) -> list[dict]:
    """最近 N 天的评测历史（趋势）。"""
    with sqlite3.connect(HISTORY_DB) as conn:
        rows = conn.execute(
            """SELECT run_at, dataset, total, hit_rate, reject_rate,
                      top_doc_hit_rate, pass_rate, avg_latency_ms
               FROM eval_history
               WHERE run_at >= datetime('now', ?)
               ORDER BY run_at DESC""",
            (f"-{days} days",),
        ).fetchall()
        columns = [d[0] for d in conn.execute("SELECT * FROM eval_history LIMIT 1").description]
        return [dict(zip(columns, r)) for r in rows]


def run_golden_eval(dataset_path: str | Path = DEFAULT_DATASET) -> dict:
    """运行 Golden Dataset 评测。

    Returns:
        {
          "total": int,
          "hit_rate": float,
          "reject_rate": float,
          "top_doc_hit_rate": float,
          "pass_rate": float,
          "avg_latency_ms": int,
          "details": [per-case results]
        }
    """
    from backend.app.api.deps import get_multi_agent  # 导入较慢，延迟到调用

    cases = load_golden(dataset_path)
    agent = get_multi_agent()
    results = []
    for case in cases:
        try:
            t0 = time.time()
            response = agent.ask(case["query"], session_id=f"golden-{case['id']}")
            latency_ms = int((time.time() - t0) * 1000)
        except Exception as e:
            response = {"answer": f"[ERROR] {e}", "sources": []}
            latency_ms = -1
        scored = evaluate_response(response, case, latency_ms)
        results.append(scored)

    summary = aggregate({"results": results})
    save_history(summary)
    return summary
