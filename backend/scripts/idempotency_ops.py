#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""idempotency_ops.py — Side-Effect 幂等 ledger 运维 CLI（在 app 容器内运行）。

用途（生产 Runbook 配套，见 docs/2026-09-24-Side-Effect-Idempotency-Production-Runbook.md）：
  - IN_DOUBT（stale running）发现与人工裁决：resolve_stale_side_effect 的
    受控封装——只允许处理租约已过期的 running 行，decision/result/reason
    全部落库（error_code 标记 + 结构化日志），禁止绕过 ledger 直改。
  - Phase3 STOP E（verifying/executing 卡死确认行）：verifying 清单 /
    inspect-confirmation 关联链 / resolve-confirmation 同事务裁决
    （账本双形态 + 确认行 + audit_logs 三方原子收敛，唯一合法出口）。
  - ledger 行只读查询（跨 actor 的运维视图；自助查询走
    GET /api/idempotency/operations/{client_key}）。
  - 探针任务投递/计数（仅测试窗口：SIDE_EFFECT_PROBE_ENABLED=1 的 worker）。

运行方式（宿主机）：
    docker exec -i agent-app-1 python - < backend/scripts/idempotency_ops.py -- status ...
（python 直连容器内配置，不在宿主机跑——避免 5432/5433 双 PG 误连。）

审批与审计约定：
  - resolve 属人工写操作：必须在工单/值班记录里登记 operator、tenant、
    key、decision、reason 后再执行；本命令不记录 operator 身份，
    reason 参数必须包含 operator 标识（如 "op:zhang cancelled-order-123"）。
"""
from __future__ import annotations

import argparse
import json
import sys


def _conn():
    import psycopg

    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    dsn = (
        f"postgresql://{c['user']}:{c['password']}"
        f"@{c['host']}:{c['port']}/{c['dbname']}"
    )
    return psycopg.connect(dsn)


def cmd_status(args) -> int:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT tenant_id, actor_id, operation, client_key, status, attempt,
                   error_code, lease_expires_at, owner_execution_id,
                   created_at, updated_at,
                   result IS NOT NULL AS has_result
            FROM ai.idempotency_records
            WHERE tenant_id = %s AND actor_id = %s
              AND operation = %s AND client_key = %s
            """,
            (args.tenant, args.actor, args.operation, args.client_key),
        )
        row = cur.fetchone()
    if row is None:
        print("NOT_FOUND")
        return 1
    cols = [
        "tenant_id", "actor_id", "operation", "client_key", "status",
        "attempt", "error_code", "lease_expires_at", "owner_execution_id",
        "created_at", "updated_at", "has_result",
    ]
    print(json.dumps(dict(zip(cols, row)), ensure_ascii=False, default=str,
                     indent=2))
    return 0


def cmd_stale(args) -> int:
    """IN_DOUBT 候选清单：running 且租约已过期。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT tenant_id, actor_id, operation, client_key, attempt,
                   owner_execution_id, lease_expires_at, created_at, updated_at
            FROM ai.idempotency_records
            WHERE status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= now()
            ORDER BY lease_expires_at
            """
        )
        rows = cur.fetchall()
    cols = ["tenant", "actor", "operation", "client_key", "attempt", "owner",
            "lease_expired_at", "created_at", "updated_at"]
    print(json.dumps([dict(zip(cols, r)) for r in rows],
                     ensure_ascii=False, default=str, indent=2))
    print(f"total={len(rows)}", file=sys.stderr)
    return 0


def cmd_resolve(args) -> int:
    from backend.shared.idempotency import resolve_stale_side_effect

    result = json.loads(args.result_json) if args.result_json else None
    ok = resolve_stale_side_effect(
        tenant_id=args.tenant,
        actor_id=args.actor,
        operation=args.operation,
        client_key=args.client_key,
        decision=args.decision,
        result=result,
        reason=args.reason,
    )
    print("RESOLVED" if ok else "NOT_RESOLVED(不存在/非 stale running/裁决过)")
    return 0 if ok else 1


def _cs_reconciliation():
    from backend.customer_service.reconciliation import (
        inspect_verifying_confirmation,
        list_verifying_confirmations,
        resolve_verifying_confirmation,
    )

    return (list_verifying_confirmations, inspect_verifying_confirmation,
            resolve_verifying_confirmation)


def cmd_verifying_list(args) -> int:
    """STOP E：verifying/executing 卡死确认行清单（含账本未决标记）。"""
    list_verifying, _, _ = _cs_reconciliation()
    rows = list_verifying(limit=args.limit)
    print(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
    print(f"total={len(rows)}", file=sys.stderr)
    return 0


def cmd_verifying_inspect(args) -> int:
    """STOP E：单条卡死确认行的完整关联链（确认行+账本+守卫语义）。"""
    _, inspect_verifying, _ = _cs_reconciliation()
    record = inspect_verifying(confirmation_id=args.confirmation_id)
    if record is None:
        print("NOT_FOUND")
        return 1
    print(json.dumps(record, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_verifying_resolve(args) -> int:
    """STOP E：裁决卡死确认行——账本+确认行+审计同事务收敛。

    decision=unresolved 只落审计、状态不动（守卫保持阻断）；
    executed/not_executed 双 CAS 原子生效，重复执行幂等返回 False。
    reason 必须含 operator 标识；executed 另须 --operator 显式身份。
    """
    _, _, resolve_verifying = _cs_reconciliation()
    result = json.loads(args.result_json) if args.result_json else None
    outcome = resolve_verifying(
        confirmation_id=args.confirmation_id,
        decision=args.decision,
        reason=args.reason,
        operator=args.operator,
        result=result,
    )
    print(json.dumps(outcome, ensure_ascii=False, default=str, indent=2))
    return 0 if outcome.get("resolved") else 1


def cmd_dispatch_probe(args) -> int:
    from backend.tasks.celery_app import celery_app

    r = celery_app.send_task(
        "tasks.side_effect_probe",
        kwargs={
            "probe_key": args.probe_key,
            "tenant_id": args.tenant,
            "actor_id": args.actor,
        },
    )
    print(f"task_id={r.id}")
    if args.wait > 0:
        try:
            print(json.dumps(r.get(timeout=args.wait), ensure_ascii=False))
        except Exception as exc:
            print(f"result_wait_failed: {exc}")
            return 2
    return 0


def cmd_probe_count(args) -> int:
    """真实副作用计数：probe 表行数 = 探针动作真实执行次数。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*), min(execution_id), max(execution_id)
            FROM ai.side_effect_probe WHERE probe_key = %s
            """,
            (args.probe_key,),
        )
        n, first_exec, last_exec = cur.fetchone()
    print(json.dumps({
        "probe_key": args.probe_key,
        "effect_count": int(n),
        "first_effect_execution": first_exec,
        "last_effect_execution": last_exec,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_key_args(sp):
        sp.add_argument("--tenant", required=True)
        sp.add_argument("--actor", required=True)
        sp.add_argument("--operation", required=True)
        sp.add_argument("--client-key", required=True)

    sp = sub.add_parser("status", help="ledger 行只读查询")
    add_key_args(sp)
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("stale", help="列出 stale running（IN_DOUBT 候选）")
    sp.set_defaults(fn=cmd_stale)

    sp = sub.add_parser("resolve", help="人工裁决 stale claim")
    add_key_args(sp)
    sp.add_argument("--decision", required=True,
                    choices=["executed", "not_executed"])
    sp.add_argument("--reason", required=True,
                    help="裁决依据，必须含 operator 标识")
    sp.add_argument("--result-json", default=None,
                    help="decision=executed 时可选的已知结果 JSON")
    sp.set_defaults(fn=cmd_resolve)

    # ── Phase3 STOP E：verifying/executing 卡死确认行（CS 域）────────
    sp = sub.add_parser(
        "verifying", help="列出 verifying/executing 卡死确认行（STOP E）")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(fn=cmd_verifying_list)

    sp = sub.add_parser(
        "inspect-confirmation",
        help="单条卡死确认行完整关联链（确认行+账本+守卫语义）")
    sp.add_argument("--confirmation-id", required=True)
    sp.set_defaults(fn=cmd_verifying_inspect)

    sp = sub.add_parser(
        "resolve-confirmation",
        help="裁决卡死确认行：账本+确认行+审计同事务收敛（STOP E 唯一出口）")
    sp.add_argument("--confirmation-id", required=True)
    sp.add_argument("--decision", required=True,
                    choices=["executed", "not_executed", "unresolved"])
    sp.add_argument("--reason", required=True,
                    help="裁决依据，必须含 operator 标识")
    sp.add_argument("--operator", default="",
                    help="裁决人身份（executed 必填，审计 actor_id）")
    sp.add_argument("--result-json", default=None,
                    help="decision=executed 时可选的已知结果 JSON")
    sp.set_defaults(fn=cmd_verifying_resolve)

    sp = sub.add_parser("dispatch-probe", help="投递探针任务（测试窗口）")
    sp.add_argument("--probe-key", required=True)
    sp.add_argument("--tenant", default="default")
    sp.add_argument("--actor", default="step6_probe")
    sp.add_argument("--wait", type=int, default=0,
                    help=">0 时等待任务结果 N 秒")
    sp.set_defaults(fn=cmd_dispatch_probe)

    sp = sub.add_parser("probe-count", help="探针真实副作用计数")
    sp.add_argument("--probe-key", required=True)
    sp.set_defaults(fn=cmd_probe_count)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
