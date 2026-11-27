"""评估用量与预算账本对账逻辑，输出 data/eval_runs/llm_usage_summary.json."""

from decimal import Decimal
import json
import os
from pathlib import Path
import psycopg


def main() -> None:
    try:
        conn = psycopg.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="postgres",
            dbname=os.environ["MEMORY_PGDATABASE"],
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT component, model, provider, cost_status, pricing_source, count(*), "
                "sum(coalesce(total_tokens, 0)), sum(coalesce(billed_cost_cny, 0)) "
                "FROM llm_usage "
                "WHERE id > %s AND component IN ('llm', 'embedding', 'rerank') "
                "GROUP BY component, model, provider, cost_status, pricing_source "
                "ORDER BY component, model, provider, cost_status, pricing_source",
                (int(os.getenv("LLM_USAGE_START_ID") or 0),),
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT count(*), "
                "count(*) FILTER (WHERE tenant_id IS DISTINCT FROM %s "
                "OR user_id IS DISTINCT FROM %s), "
                "sum(coalesce(total_tokens, 0)), "
                "sum(coalesce(billed_cost_cny, 0)) "
                "FROM llm_usage WHERE id > %s "
                "AND component IN ('llm', 'embedding', 'rerank')",
                (
                    "eval",
                    os.environ["EVAL_TRIGGERED_BY"],
                    int(os.getenv("LLM_USAGE_START_ID") or 0),
                ),
            )
            usage_totals = cur.fetchone()
            cur.execute(
                "WITH reservation_per_call AS ("
                "SELECT DISTINCT ON (id) id, status, reserved_cny, settled_cny "
                "FROM budget_reservations WHERE request_id = %s "
                "ORDER BY id, created_at) "
                "SELECT status, count(*), sum(reserved_cny), sum(settled_cny) "
                "FROM reservation_per_call GROUP BY status ORDER BY status",
                (f"eval:{os.environ['EVAL_RUN_ID']}",),
            )
            reservation_rows = cur.fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
        print("[cost] usage/quota ledger 查询失败；对账标记为 UNVERIFIED")
        rows = []
        reservation_rows = []
        usage_totals = (0, 0, 0, 0)
        query_failed = True
    else:
        query_failed = False

    unverified_statuses = {"", "unpriced", "price_unknown"}
    unverified_calls = sum(
        int(r[5] or 0) for r in rows if (r[3] or "").lower() in unverified_statuses
    )
    estimated_calls = sum(
        int(r[5] or 0) for r in rows if (r[3] or "").lower() == "estimated"
    )
    cost_verification = (
        "UNVERIFIED"
        if query_failed or not rows or unverified_calls
        else "ESTIMATED"
        if estimated_calls
        else "VERIFIED"
    )

    reservation_counts = (
        {str(r[0]): int(r[1] or 0) for r in reservation_rows}
        if not query_failed
        else {}
    )
    reservation_amounts_cny = (
        {
            str(status): {
                "reserved_cny": round(
                    sum(
                        float(r[2] or 0) for r in reservation_rows if r[0] == status
                    ),
                    6,
                ),
                "settled_cny": round(
                    sum(
                        float(r[3] or 0) for r in reservation_rows if r[0] == status
                    ),
                    6,
                ),
            }
            for status in reservation_counts
        }
        if not query_failed
        else {}
    )
    reservation_total = sum(reservation_counts.values())
    settled_cost = (
        sum(
            (Decimal(str(r[3] or 0)) for r in reservation_rows if r[0] == "settled"),
            Decimal("0"),
        )
        if not query_failed
        else Decimal("0")
    )

    usage_calls = int(usage_totals[0] or 0) if not query_failed else 0
    unattributed_calls = int(usage_totals[1] or 0) if not query_failed else 0
    usage_tokens = int(usage_totals[2] or 0) if not query_failed else 0
    usage_cost = (
        Decimal(str(usage_totals[3] or 0)) if not query_failed else Decimal("0")
    )
    ledger_difference = abs(usage_cost - settled_cost)

    budget_envelope_verification = (
        "WITHIN_LIMITS"
        if not query_failed
        and usage_calls <= 200
        and usage_tokens <= 200000
        and usage_cost <= Decimal("0.50")
        else "OVER_LIMIT"
    )
    budget_ledger_verification = (
        "VERIFIED"
        if not query_failed
        and os.environ.get("LLM_BUDGET_MODE") == "enforce"
        and usage_calls > 0
        and reservation_total == usage_calls
        and reservation_counts.get("reserved", 0) == 0
        and reservation_counts.get("needs_review", 0) == 0
        and unattributed_calls == 0
        and ledger_difference <= Decimal("0.000001")
        else "UNVERIFIED"
    )

    summary = []
    for r in rows:
        cost_status = (r[3] or "").lower()
        summary.append(
            {
                "component": r[0],
                "model": r[1],
                "provider": r[2],
                "cost_status": cost_status or "unknown",
                "pricing_source": r[4] or "unknown",
                "calls": int(r[5] or 0),
                "total_tokens": int(r[6] or 0),
                "billed_cost_cny": (
                    None
                    if cost_status in unverified_statuses
                    else round(float(r[7] or 0), 6)
                ),
            }
        )

    out_dir = Path("data/eval_runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "note": "隔离测试库账本，未同步生产计费",
        "eval_run_id": os.environ.get("EVAL_RUN_ID", ""),
        "budget_request_id": f"eval:{os.environ.get('EVAL_RUN_ID', '')}",
        "budget_mode": os.environ.get("LLM_BUDGET_MODE", ""),
        "cost_verification": cost_verification,
        "budget_ledger_verification": budget_ledger_verification,
        "verification_error": "ledger_query_failed" if query_failed else None,
        "unverified_calls": unverified_calls,
        "usage_calls": usage_calls,
        "usage_tokens": usage_tokens,
        "unattributed_calls": unattributed_calls,
        "budget_envelope_verification": budget_envelope_verification,
        "budget_reservations": reservation_counts,
        "reservation_amounts_cny": reservation_amounts_cny,
        "usage_cost_cny": round(float(usage_cost), 6),
        "settled_cost_cny": round(float(settled_cost), 6),
        "ledger_difference_cny": round(float(ledger_difference), 6),
        "usage": summary,
    }
    (out_dir / "llm_usage_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
