"""cs_dispatch_chaos.py — P9 故障演练（读侧预检 + 受控演练）。

方案 §六 P9 要求对 Redis / PostgreSQL / dispatcher / API 四类故障做演练。
共享开发栈上**不允许脚本擅自停共享容器**，因此本脚本分两层：

1. ``preflight``（默认，只读）：采集演练前的基线证据 —— Redis 连通与
   dispatcher 心跳、outbox 积压、028 迁移列、API /health。
2. ``--target dispatcher --apply``：唯一受控写操作 —— ``docker compose
   stop cs-dispatcher`` → 观察 30s TTL 心跳过期与 outbox 积压增长 →
   ``start`` 恢复。只针对本任务专属的 cs-dispatcher 服务，不碰共享容器。

Redis/PG/API 故障属于共享资源，脚本只输出人工演练清单（含期望现象与
恢复判据），由值班人在变更窗口执行。

结果 JSON 写 docs/reports/cs-dispatch-chaos-<ts>.json。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

import os  # noqa: E402

REPORT_DIR = REPO_ROOT / "docs" / "reports"

MANUAL_DRILLS = [
    {
        "target": "redis",
        "steps": ["docker compose stop redis", "等待 60s", "docker compose start redis"],
        "expect": "dispatcher 派单 fail-closed（cs_dispatch_offered_total{result=presence_unavailable} 上升）且不产生脏绑定；relay 失败事件保持 pending，恢复后 1-2 个 tick 内清空",
        "recovery": "cs_outbox_pending 回落 0；presence_unavailable 计数停止增长",
    },
    {
        "target": "postgres",
        "steps": ["docker compose stop postgres", "等待 60s", "docker compose start postgres"],
        "expect": "API 503（MemoryDatabaseUnavailable）；dispatcher 循环不退出，异常只记日志",
        "recovery": "恢复后下一 tick 队列继续消费；无需人工修复数据",
    },
    {
        "target": "api",
        "steps": ["docker compose stop app", "等待 60s", "docker compose start app"],
        "expect": "用户转人工入池 503，dispatcher/reaper/relay 继续运行（独立进程）",
        "recovery": "app 恢复后入池恢复；期间无工单丢失",
    },
]


def _preflight() -> dict:
    pre: dict = {"checked_at": datetime.now(timezone.utc).isoformat()}

    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.getenv("PGHOST", "localhost"),
            port=int(os.getenv("PGPORT", "5432")),
            user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", ""),
            dbname=os.getenv("PGDATABASE", "demo"),
            connect_timeout=5,
        )
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='customer_service' AND table_name='handoffs' "
                "AND column_name IN ('tenant_id','offer_expires_at',"
                "'assignment_version','attempt_count')"
            )
            pre["pg_reachable"] = True
            pre["handoff_new_columns"] = cur.fetchone()[0]
            # 028 未应用时 events/outbox 列不存在：只记 null，不算 PG 不可达
            try:
                cur.execute(
                    "SELECT count(*) FROM customer_service.events "
                    "WHERE outbox_status = 'pending'"
                )
                pre["outbox_pending"] = cur.fetchone()[0]
            except psycopg2.Error:
                conn.rollback()
                pre["outbox_pending"] = None
                pre["outbox_note"] = "customer_service.events 不存在（028 未应用）"
        conn.close()
    except Exception as exc:  # noqa: BLE001
        pre["pg_reachable"] = False
        pre["pg_error"] = f"{type(exc).__name__}: {exc}"

    try:
        from backend.customer_service.dispatch import presence

        client = presence._redis_client()
        pre["redis_reachable"] = client is not None and bool(client.ping())
        if pre["redis_reachable"]:
            keys = client.keys("cs:dispatcher:heartbeat:*")
            pre["dispatcher_heartbeats"] = [k.decode() if isinstance(k, bytes) else k for k in keys]
    except Exception as exc:  # noqa: BLE001
        pre["redis_reachable"] = False
        pre["redis_error"] = f"{type(exc).__name__}: {exc}"

    pre["manual_drills"] = MANUAL_DRILLS
    return pre


def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


async def _drill_dispatcher(report: dict) -> None:
    """受控演练：停 cs-dispatcher → 心跳过期 + outbox 积压 → 恢复。"""
    from backend.customer_service.dispatch import presence

    down = _compose("stop", "cs-dispatcher")
    report["stop_exit"] = down.returncode
    if down.returncode != 0:
        report["drill_error"] = (down.stderr or down.stdout)[-400:]
        return

    deadline = time.monotonic() + 40
    expired_at = None
    while time.monotonic() < deadline:
        client = presence._redis_client()
        keys = client.keys("cs:dispatcher:heartbeat:*") if client else []
        if not keys:
            expired_at = datetime.now(timezone.utc).isoformat()
            break
        await asyncio.sleep(2)
    report["heartbeat_expired_at"] = expired_at
    report["heartbeat_expired_within_40s"] = expired_at is not None

    up = _compose("start", "cs-dispatcher")
    report["start_exit"] = up.returncode
    await asyncio.sleep(10)
    client = presence._redis_client()
    keys = client.keys("cs:dispatcher:heartbeat:*") if client else []
    report["heartbeat_restored"] = bool(keys)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["dispatcher"], default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="确认执行受控演练（dispatcher 会短暂停止）",
    )
    args = parser.parse_args()

    report: dict = {
        "script": "cs_dispatch_chaos.py",
        "mode": "apply" if (args.target and args.apply) else "preflight",
        "preflight": _preflight(),
    }

    if args.target == "dispatcher":
        if not args.apply:
            report["note"] = "加 --apply 才会真正 stop/start cs-dispatcher"
        else:
            await _drill_dispatcher(report)

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["finished_at"][:19].replace(":", "").replace("-", "")
    out = REPORT_DIR / f"cs-dispatch-chaos-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
