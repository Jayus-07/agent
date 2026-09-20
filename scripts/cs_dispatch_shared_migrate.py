"""cs_dispatch_shared_migrate.py — 共享库 028/029 迁移预检与受控执行。

背景：客服派单/RBAC 任务的 ``backend/sql/migrations/028_cs_dispatch.sql`` 与
``029_rbac_audit.sql`` 尚未部署到共享库（P9 登记的生产门槛）。本脚本把变更
窗口里的「预检 → 执行 → 复核」三步固化，避免手工 psql 漏步骤：

1. ``preflight``（默认，**只读**）：对目标库逐项检查 028/029 的关键对象
   （列 / 索引 / 约束 / 表），输出缺失清单与结论 JSON。不写任何数据。
2. ``--apply --confirm``：按 028 → 029 顺序执行，每文件单事务
   （``ON_ERROR_STOP=1``），任一文件失败即停。执行后自动复核并落报告。

⚠️ 028 并非纯加法：含一条存量数据 UPDATE（assignments 无 handoff_id 的
offered/accepted 降级为 released）与孤儿数据 RAISE EXCEPTION 检查（会主动
阻断迁移）。执行前必须完成 pg_dump 备份（见
部署/cs-dispatch-变更窗口执行手册.md 门槛一）。

用法：
    python scripts/cs_dispatch_shared_migrate.py                 # 只读预检
    python scripts/cs_dispatch_shared_migrate.py --json          # 预检 + JSON 报告
    python scripts/cs_dispatch_shared_migrate.py --apply --confirm --operator <标识>

连接方式：经 ``docker exec <容器> psql`` 直连（容器名默认
``agent-postgres-1``，可用 ``--container`` 覆盖；库名默认
``agent_memory``，可用 ``--database`` 覆盖）。不在宿主机装驱动。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

REPORT_DIR = REPO_ROOT / "docs" / "reports"
MIGRATIONS = [
    REPO_ROOT / "backend" / "sql" / "migrations" / "028_cs_dispatch.sql",
    REPO_ROOT / "backend" / "sql" / "migrations" / "029_rbac_audit.sql",
]

# (迁移号, 类别, 名称, 判定 SQL：返回行数 >0 即视为已存在)
CHECKS = [
    ("028", "column", "customer_service.handoffs.priority",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='handoffs' AND column_name='priority'"),
    ("028", "column", "customer_service.handoffs.assignment_version",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='handoffs' AND column_name='assignment_version'"),
    ("028", "column", "customer_service.handoffs.attempt_count",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='handoffs' AND column_name='attempt_count'"),
    ("028", "column", "customer_service.handoffs.offer_expires_at",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='handoffs' AND column_name='offer_expires_at'"),
    ("028", "column", "customer_service.cs_agents.tenant_id",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='cs_agents' AND column_name='tenant_id'"),
    ("028", "column", "customer_service.cs_agents.auth_user_id",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='cs_agents' AND column_name='auth_user_id'"),
    ("028", "column", "customer_service.assignments.state",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='assignments' AND column_name='state'"),
    ("028", "column", "customer_service.assignments.attempt_no",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='assignments' AND column_name='attempt_no'"),
    ("028", "column", "customer_service.events.outbox_status",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='events' AND column_name='outbox_status'"),
    ("028", "column", "customer_service.events.event_seq",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='events' AND column_name='event_seq'"),
    ("028", "column", "customer_service.conversations.tenant_id",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='customer_service' AND table_name='conversations' AND column_name='tenant_id'"),
    ("028", "index", "idx_cs_handoff_dispatch_queue",
     "SELECT 1 FROM pg_indexes WHERE schemaname='customer_service' AND indexname='idx_cs_handoff_dispatch_queue'"),
    ("028", "index", "uq_cs_handoff_tenant_conversation_active",
     "SELECT 1 FROM pg_indexes WHERE schemaname='customer_service' AND indexname='uq_cs_handoff_tenant_conversation_active'"),
    ("028", "index", "uq_cs_agent_tenant_auth_user",
     "SELECT 1 FROM pg_indexes WHERE schemaname='customer_service' AND indexname='uq_cs_agent_tenant_auth_user'"),
    ("028", "index", "idx_cs_event_outbox_pending",
     "SELECT 1 FROM pg_indexes WHERE schemaname='customer_service' AND indexname='idx_cs_event_outbox_pending'"),
    ("028", "constraint", "fk_cs_handoff_tenant_agent",
     "SELECT 1 FROM pg_constraint WHERE conrelid='customer_service.handoffs'::regclass AND conname='fk_cs_handoff_tenant_agent'"),
    ("028", "constraint", "fk_cs_assignment_tenant_conversation",
     "SELECT 1 FROM pg_constraint WHERE conrelid='customer_service.assignments'::regclass AND conname='fk_cs_assignment_tenant_conversation'"),
    ("028", "constraint", "ck_cs_assignment_active_handoff_required",
     "SELECT 1 FROM pg_constraint WHERE conrelid='customer_service.assignments'::regclass AND conname='ck_cs_assignment_active_handoff_required'"),
    ("029", "column", "auth.users.version",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='auth' AND table_name='users' AND column_name='version'"),
    ("029", "column", "auth.users.tenant_id",
     "SELECT 1 FROM information_schema.columns WHERE table_schema='auth' AND table_name='users' AND column_name='tenant_id'"),
    ("029", "table", "auth.rbac_audits",
     "SELECT 1 FROM pg_tables WHERE schemaname='auth' AND tablename='rbac_audits'"),
    ("029", "index", "idx_rbac_audits_tenant_created",
     "SELECT 1 FROM pg_indexes WHERE schemaname='auth' AND indexname='idx_rbac_audits_tenant_created'"),
]

# 028 执行前的数据风险预检（只读）：结果非零行则 --apply 前必须人工确认。
RISK_CHECKS = [
    ("assignments_legacy_active_rows",
     "SELECT COUNT(*) FROM customer_service.assignments WHERE handoff_id IS NULL AND state IN ('offered','accepted')"),
    ("handoffs_orphan_tenant_agent",
     "SELECT COUNT(*) FROM customer_service.handoffs h WHERE h.assigned_agent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM customer_service.cs_agents a WHERE a.tenant_id=h.tenant_id AND a.agent_id=h.assigned_agent_id)"),
    ("handoffs_duplicate_active",
     "SELECT COUNT(*) FROM (SELECT 1 FROM customer_service.handoffs WHERE handoff_state<>'closed' GROUP BY tenant_id, conversation_id HAVING COUNT(*)>1) t"),
    ("cs_agents_duplicate_auth_user",
     "SELECT COUNT(*) FROM (SELECT 1 FROM customer_service.cs_agents WHERE auth_user_id IS NOT NULL GROUP BY tenant_id, auth_user_id HAVING COUNT(*)>1) t"),
]


def _psql(container: str, database: str, sql: str) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "postgres", "-d", database,
         "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def _apply_file(container: str, database: str, path: Path) -> str:
    """单事务执行一个迁移文件（stdin 灌入，ON_ERROR_STOP=1）。"""
    sql = path.read_text(encoding="utf-8")
    result = subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", database,
         "-v", "ON_ERROR_STOP=1", "--single-transaction", "-f", "-"],
        input=sql, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{path.name} failed: {result.stderr.strip()[:800]}")
    return result.stdout.strip()[-400:]


def run_preflight(container: str, database: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    report: dict = {"timestamp": now, "container": container, "database": database,
                    "read_only": True, "migrations": {}}
    for mig in ("028", "029"):
        checks = []
        for m, cat, name, query in CHECKS:
            if m != mig:
                continue
            try:
                present = bool(_psql(container, database, query))
            except RuntimeError as exc:
                present, err = False, str(exc)
            else:
                err = None
            checks.append({"category": cat, "name": name,
                           "present": present, "error": err})
        missing = [c["name"] for c in checks if not c["present"]]
        report["migrations"][mig] = {
            "total": len(checks),
            "present": len(checks) - len(missing),
            "missing": missing,
            "applied": not missing,
            "checks": checks,
        }
    # 数据风险项（仅 028 相关）。纯存量 schema（028 未执行过）缺新列属预期：
    # 028 先补列再自带孤儿/重复 RAISE EXCEPTION 检查，此时记 N/A 不阻断。
    risks = {}
    for name, query in RISK_CHECKS:
        try:
            risks[name] = int(_psql(container, database, query) or "0")
        except RuntimeError as exc:
            msg = str(exc)
            risks[name] = ("N/A(存量schema缺列,由028自检兜底)" if "does not exist" in msg
                           else f"ERROR: {msg}")
    report["risk_items"] = risks
    report["conclusion"] = (
        "ALL_APPLIED" if all(report["migrations"][m]["applied"] for m in ("028", "029"))
        else "PENDING_DEPLOY"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--container", default="agent-postgres-1",
                        help="PG 容器名（默认 agent-postgres-1）")
    parser.add_argument("--database", default="agent_memory",
                        help="目标库名（默认 agent_memory）")
    parser.add_argument("--apply", action="store_true", help="执行迁移（默认只预检）")
    parser.add_argument("--confirm", action="store_true",
                        help="--apply 的二次确认开关，防误触")
    parser.add_argument("--operator", default="", help="执行人标识（写入审计报告）")
    parser.add_argument("--json", action="store_true", help="落盘 JSON 报告")
    args = parser.parse_args()

    if args.apply and not args.confirm:
        print("拒绝执行：--apply 必须与 --confirm 同用，且需先完成 pg_dump 备份"
              "（见 部署/cs-dispatch-变更窗口执行手册.md 门槛一）。", file=sys.stderr)
        return 2

    pre = run_preflight(args.container, args.database)
    print(f"== 预检结论: {pre['conclusion']} (db={args.database}) ==")
    for mig, info in pre["migrations"].items():
        state = "已部署" if info["applied"] else f"缺失 {len(info['missing'])} 项: {', '.join(info['missing'])}"
        print(f"  {mig}: {info['present']}/{info['total']} 命中 — {state}")
    print(f"  风险项: {pre['risk_items']}")

    if args.json:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        out = REPORT_DIR / f"cs-dispatch-migrate-preflight-{ts}.json"
        out.write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告: {out}")

    if not args.apply:
        return 0

    if pre["conclusion"] == "ALL_APPLIED":
        print("两个迁移均已部署，无需执行。")
        return 0
    bad = [k for k, v in pre["risk_items"].items()
           if isinstance(v, int) and v > 0 or isinstance(v, str) and v.startswith("ERROR")]
    if bad:
        print(f"存在数据风险项非零/异常：{bad} —— 028 的孤儿检查会 RAISE EXCEPTION 阻断，"
              "先人工处置再执行。", file=sys.stderr)
        return 3

    now = datetime.now(timezone.utc).isoformat()
    result: dict = {"timestamp": now, "operator": args.operator,
                    "container": args.container, "database": args.database,
                    "files": []}
    for path in MIGRATIONS:
        print(f"== 执行 {path.name} ==")
        tail = _apply_file(args.container, args.database, path)
        result["files"].append({"name": path.name, "ok": True, "tail": tail})
        print(f"   OK（输出尾部: {tail[-120:]}）")

    post = run_preflight(args.container, args.database)
    result["post_preflight"] = post
    print(f"== 复核结论: {post['conclusion']} ==")
    if args.json or True:  # apply 必落报告
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        out = REPORT_DIR / f"cs-dispatch-migrate-apply-{ts}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告: {out}")
    return 0 if post["conclusion"] == "ALL_APPLIED" else 1


if __name__ == "__main__":
    sys.exit(main())
