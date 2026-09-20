"""cs_dispatch_pg_acceptance.py — P9 真实 PostgreSQL 派单验收。

在**临时数据库**（cs_dispatch_acceptance_<hex>，跑完即 drop）上应用
``backend/sql/migrations/028_cs_dispatch.sql``，然后用真实并发事务执行三组
方案 §六 P9 验收：

A. 并发绑定正确性 —— 多 dispatcher 任务并发派单：每张工单恰有一条活动
   assignment、绑定与 assignment 一致、绝不超容量。
B. 容量上限 —— 单坐席 max_conversations=2 时，10 张工单最多派出 2 张。
C. 负载均衡 —— 等容量 3 坐席 15 张工单，活动数极差 ≤ 1（轮询公平）。

用法：
    D:/Python/python.exe scripts/cs_dispatch_pg_acceptance.py

结果 JSON 写入 docs/reports/cs-dispatch-pg-acceptance-<ts>.json。
Redis presence 在脚本进程内以 monkeypatch 方式提供（全部在线）——Redis
判定路径已由单元测试覆盖，本验收聚焦 PostgreSQL 并发语义。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

import psycopg2  # noqa: E402

REPORT_DIR = REPO_ROOT / "docs" / "reports"
MIGRATIONS = [
    # 真实 schema 链：002 建记忆库核心表 → 006 建客服核心表 →
    # 020 补 ORM 列/新表 → 028 派单契约
    REPO_ROOT / "backend" / "sql" / "migrations" / "002_agent_memory_schema.sql",
    REPO_ROOT / "backend" / "sql" / "migrations" / "006_customer_service.sql",
    REPO_ROOT / "backend" / "sql" / "migrations" / "014_cs_rating.sql",
    REPO_ROOT / "backend" / "sql" / "migrations" / "020_alembic_gaps_pg.sql",
    REPO_ROOT / "backend" / "sql" / "migrations" / "028_cs_dispatch.sql",
]


def _admin_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
        dbname="postgres",
        connect_timeout=5,
    )


def _temp_db_name() -> str:
    tag = hashlib.sha1(secrets.token_hex(8).encode()).hexdigest()[:10]
    return f"cs_dispatch_acceptance_{tag}"


def _apply_migration(dbname: str) -> None:
    conn = psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
        dbname=dbname,
        connect_timeout=5,
    )
    try:
        with conn, conn.cursor() as cur:
            for path in MIGRATIONS:
                cur.execute(path.read_text(encoding="utf-8"))
    finally:
        conn.close()


async def _run_group(
    sessionmaker,
    group: str,
    tenant_id: str,
    agents: list,
    handoffs: list[dict],
    concurrency: int,
) -> dict:
    """在真实 PG 上以 ``concurrency`` 个并发任务跑派单直到队列清空。

    每组使用独立租户，避免上一组遗留的 waiting 工单污染本组计数。
    """
    from sqlalchemy import select

    from backend.customer_service.dispatch import presence, service
    from backend.customer_service.models.assignment import CSAssignment
    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.handoff import CSHandoff
    now0 = datetime.now(timezone.utc)

    # presence：全部候选在线（Redis 判定由单元测试覆盖）
    async def all_online(*_a, agent_ids=None, **_k):
        return set(agent_ids or [])

    presence.online_agent_ids = all_online

    async with sessionmaker() as session:
        async with session.begin():
            for agent in agents:
                session.add(agent)
            for item in handoffs:
                conv = CSConversation(
                    conversation_id=item["conversation_id"],
                    user_id=item["user_id"],
                    tenant_id=tenant_id,
                    conversation_status="open",
                    handling_mode="waiting_human",
                )
                session.add(conv)
                session.add(
                    CSHandoff(
                        handoff_id=item["handoff_id"],
                        conversation_id=item["conversation_id"],
                        user_id=item["user_id"],
                        tenant_id=tenant_id,
                        handoff_state="waiting_human",
                        trigger_type="explicit_request",
                        priority=50,
                        created_at=now0,
                        updated_at=now0,
                        total_deadline_at=now0.replace(year=now0.year + 1),
                    )
                )

    statuses: list[str] = []

    async def worker() -> None:
        while True:
            async with sessionmaker() as session:
                result = await service.dispatch_once(
                    session, tenant_id=tenant_id, now=datetime.now(timezone.utc)
                )
            statuses.append(result.status)
            if result.status in {"no_handoff", "no_candidate", "presence_unavailable"}:
                return

    await asyncio.gather(*(worker() for _ in range(concurrency)))

    # 核对
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    CSAssignment.handoff_id,
                    CSAssignment.agent_id,
                    CSAssignment.state,
                ).where(CSAssignment.tenant_id == tenant_id)
            )
        ).all()
        hrows = (
            await session.execute(
                select(
                    CSHandoff.handoff_id,
                    CSHandoff.assigned_agent_id,
                    CSHandoff.handoff_state,
                ).where(CSHandoff.tenant_id == tenant_id)
            )
        ).all()

    active: dict[str, set[str]] = {}
    for handoff_id, agent_id, state in rows:
        if state in ("offered", "accepted"):
            active.setdefault(str(handoff_id), set()).add(str(agent_id))

    duplicated = [h for h, agents in active.items() if len(agents) > 1]
    over_capacity = False
    load: dict[str, int] = {}
    for _h, agent_id in ((h, next(iter(a))) for h, a in active.items()):
        load[agent_id] = load.get(agent_id, 0) + 1
    for agent in agents:
        if load.get(str(agent.agent_id), 0) > int(agent.max_conversations):
            over_capacity = True

    mismatched = [
        (str(hid), str(aid))
        for hid, aid, state in hrows
        if state == "agent_offered" and str(aid) not in active.get(str(hid), set())
    ]

    return {
        "group": group,
        "tenant_id": tenant_id,
        "statuses": {s: statuses.count(s) for s in set(statuses)},
        "active_assignments": len(active),
        "duplicated_handoffs": duplicated,
        "over_capacity": over_capacity,
        "per_agent_load": load,
        "state_assignment_mismatch": mismatched[:10],
    }


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dbname = _temp_db_name()
    report: dict = {
        "script": "cs_dispatch_pg_acceptance.py",
        "database": dbname,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "groups": [],
        "passed": False,
    }
    admin = _admin_conn()
    try:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{dbname}"')
        admin.close()

        _apply_migration(dbname)
        report["migration_applied"] = True

        dsn = (
            f"postgresql+asyncpg://{os.getenv('PGUSER', 'postgres')}:"
            f"{os.getenv('PGPASSWORD', '')}@{os.getenv('PGHOST', 'localhost')}:"
            f"{os.getenv('PGPORT', '5432')}/{dbname}"
        )
        engine = create_async_engine(dsn, pool_size=20, max_overflow=10)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        from backend.customer_service.models.agent import CSAgent

        def agent(agent_id: str, tenant_id: str, cap: int = 10) -> CSAgent:
            return CSAgent(
                agent_id=agent_id,
                tenant_id=tenant_id,
                display_name=agent_id,
                role="agent",
                enabled=True,
                available=True,
                accepting=True,
                max_conversations=cap,
            )

        def handoff_item(i: int) -> dict:
            return {
                "handoff_id": f"hd-{i:04d}",
                "conversation_id": f"conv-{i:04d}",
                "user_id": f"user-{i:04d}",
            }

        # A 并发绑定：40 工单 × 8 并发
        group_a = await _run_group(
            sessionmaker,
            "A_concurrent_binding",
            "acceptance-A",
            [agent(f"a-{i}", "acceptance-A") for i in range(4)],
            [handoff_item(i) for i in range(40)],
            concurrency=8,
        )
        group_a["passed"] = (
            not group_a["duplicated_handoffs"]
            and not group_a["over_capacity"]
            and not group_a["state_assignment_mismatch"]
        )
        report["groups"].append(group_a)

        # B 容量：1 坐席 cap=2，10 工单
        group_b = await _run_group(
            sessionmaker,
            "B_capacity_limit",
            "acceptance-B",
            [agent("b-only", "acceptance-B", cap=2)],
            [handoff_item(100 + i) for i in range(10)],
            concurrency=2,
        )
        dispatched_b = group_b["statuses"].get("dispatched", 0)
        group_b["passed"] = (
            dispatched_b == 2 and not group_b["over_capacity"]
        )
        report["groups"].append(group_b)

        # C 负载均衡：3 坐席 cap=10，15 工单 → 极差 ≤ 1
        group_c = await _run_group(
            sessionmaker,
            "C_load_balancing",
            "acceptance-C",
            [agent(f"c-{i}", "acceptance-C") for i in range(3)],
            [handoff_item(200 + i) for i in range(15)],
            concurrency=3,
        )
        loads = list(group_c["per_agent_load"].values())
        group_c["passed"] = bool(loads) and max(loads) - min(loads) <= 1 and sum(loads) == 15
        report["groups"].append(group_c)

        await engine.dispose()

        report["passed"] = all(g["passed"] for g in report["groups"])
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["passed"] = False
    finally:
        try:
            conn = _admin_conn()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (dbname,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            conn.close()
            report["temp_db_dropped"] = True
        except Exception:  # noqa: BLE001
            report["temp_db_dropped"] = False

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"cs-dispatch-pg-acceptance-{report['started_at'][:10]}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report -> {out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
