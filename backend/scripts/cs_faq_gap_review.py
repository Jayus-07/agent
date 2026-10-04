"""cs_faq_gap_review.py — FAQ 缺口周检（C6 批量化运行件，2026-10-04）。

职责：拉取近 N 天 ai.cs_faq_query_log 的 matched=false 真实 miss，输出
**人工判定清单**（读只不写——判定与发布是人审后的动作，流程见
docs/cs-runbook.md §三）：

  - 聚合频次与首末出现时间（优先处置高频）；
  - 现配对：用当前 FAQ 索引重放每条 miss——已能命中的标记 closed_now
    （早前 miss、后来补条目已覆盖），仍 miss 的才需要动作；
  - KB 线索：对 miss 剔疑问功能词后取关键词在 chunk_store 粗筛（LIKE，
    仅 active 文档），定位可能承载答案的源文档，供判「补条目/变体」
    还是「登记待补知识清单」。

口径：
  - 测试垃圾过滤：规范化后长度 <4 的问题标记 excluded（如探活 'diag'）；
  - 数据与质检日报 metrics.faq.top_miss 同源（滚动 7 天），日报看趋势、
    本脚本出处置清单；
  - Celery beat 挂载（maintenance 队列，任务名 cs.faq_gap_review）待
    celery_app.py / queue_router.py 混线解锁后登记，当前手动触发。

用法（仓库根，PG 显式 5433）：
  PGPORT=5433 python -m backend.scripts.cs_faq_gap_review
  PGPORT=5433 python -m backend.scripts.cs_faq_gap_review --days 14 --out d:/tmp/cs_gap.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from backend.customer_service.faq import FAQStore, _STOPWORDS_RE, normalize_question

_MIN_NORM_LEN = 4  # 规范化后短于该值视为探活/垃圾流量，不入清单


def _is_probe(q_norm: str) -> bool:
    """探活/垃圾判定：过短中文（<4 字）或过短纯 ASCII（<8 字符，如 'diag'）。"""
    if len(q_norm) < _MIN_NORM_LEN:
        return True
    return q_norm.isascii() and len(q_norm) < 8


def fetch_misses(conn, days: int, limit: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT question, count(*) AS cnt, min(created_at), max(created_at) "
            "FROM ai.cs_faq_query_log "
            "WHERE matched = false AND created_at >= now() - (%(days)s * interval '1 day') "
            "GROUP BY question ORDER BY cnt DESC, question LIMIT %(limit)s",
            {"days": days, "limit": limit},
        )
        return [
            {
                "question": r[0],
                "count": int(r[1]),
                "first_seen": r[2].isoformat() if r[2] else None,
                "last_seen": r[3].isoformat() if r[3] else None,
            }
            for r in cur.fetchall()
        ]


def kb_hints(conn, question: str, per_hint_limit: int = 3) -> list[dict]:
    """KB 粗筛：剔疑问功能词后取前两个关键词片段 LIKE chunk_store（只读）。"""
    stripped = _STOPWORDS_RE.sub("", normalize_question(question))
    # 关键词片段：2 字滑窗取前两个（粗筛定位用，召回交给 RAG 链语义检索）
    frags = [stripped[i:i + 2] for i in range(0, min(len(stripped), 4), 2)]
    frags = [f for f in frags if f]
    if not frags:
        return []
    like = " OR ".join(["c.content LIKE %s"] * len(frags))
    params = [f"%{f}%" for f in frags]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT c.doc_id, left(c.content, 120) "
            f"FROM chunk_store c JOIN doc_registry d "
            f"ON d.doc_id = c.doc_id AND d.status = 'active' WHERE {like} "
            f"LIMIT %s",
            params + [per_hint_limit],
        )
        return [{"doc_id": r[0], "excerpt": r[1]} for r in cur.fetchall()]


def collect_report(conn, days: int = 7, limit: int = 50) -> dict:
    """主聚合：miss 清单 + 现配对状态 + KB 线索（纯只读）。"""
    store = FAQStore(conn_factory=lambda: conn)
    store._ensure_tables()  # 幂等建表（全新环境直跑不炸）；只读后续查询
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_days": days,
        "gaps": [],
        "excluded": [],
    }
    for row in fetch_misses(conn, days, limit):
        item = dict(row)
        if _is_probe(normalize_question(row["question"])):
            item["status"] = "excluded"
            item["reason"] = "探活/垃圾流量（规范化后长度不足）"
            report["excluded"].append(item)
            continue
        m = store.match(row["question"])
        if m is not None:
            item["status"] = "closed_now"
            item["faq_id"] = m.faq_id
        else:
            item["status"] = "open"
            item["kb_hints"] = kb_hints(conn, row["question"])
        report["gaps"].append(item)
    report["open_count"] = sum(1 for g in report["gaps"] if g["status"] == "open")
    report["closed_now_count"] = sum(
        1 for g in report["gaps"] if g["status"] == "closed_now")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FAQ 缺口周检（只读清单）")
    parser.add_argument("--days", type=int, default=7, help="回看窗口天数（默认 7）")
    parser.add_argument("--limit", type=int, default=50, help="最多输出的 miss 问题数")
    parser.add_argument("--out", type=str, default="", help="可选：JSON 落盘路径")
    args = parser.parse_args(argv)

    import psycopg2

    from backend.config.database import DOC_REGISTRY_PG_CONFIG

    conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG)
    try:
        report = collect_report(conn, args.days, args.limit)
    finally:
        conn.rollback()
        conn.close()

    print(f"窗口={args.days}天 open={report['open_count']} "
          f"closed_now={report['closed_now_count']} excluded={len(report['excluded'])}")
    for g in report["gaps"]:
        line = f"[{g['status']}] {g['count']}x {g['question']}"
        if g["status"] == "closed_now":
            line += f" (faq_id={g['faq_id']})"
        else:
            docs = ", ".join(h["doc_id"] for h in g.get("kb_hints", []))
            line += f" (KB线索: {docs or '无——登记待补知识清单'})"
        print(line)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f"JSON 已落盘: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
