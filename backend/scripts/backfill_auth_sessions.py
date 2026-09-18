"""backfill_auth_sessions.py — 存量 refresh token 按 user+device 归组到 auth.sessions（023 配套）

背景（2026-09-19 会话实体改造）：023 之前签发的 refresh_tokens 行没有 session_id。
本脚本把**未吊销**的存量行按 (user_id, device_id) 分组补建 auth.sessions 并回填；
无法归组（device_id 为空）的行置 revoked=TRUE（用户重新登录一次即可，成本可接受）。

用法（连 agent_memory，读 MEMORY_PG* 环境变量，与 .env 同源）：
    python backend/scripts/backfill_auth_sessions.py --dry-run   # 只输出报告，不写库
    python backend/scripts/backfill_auth_sessions.py --execute   # 执行归组与吊销
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2  # noqa: E402

from backend.config.database import MEMORY_DB_CONFIG  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只输出报告，不写库")
    group.add_argument("--execute", action="store_true", help="执行归组与吊销")
    args = parser.parse_args()

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM auth.refresh_tokens "
                "WHERE revoked = FALSE AND session_id IS NULL")
            total, = cur.fetchone()
            cur.execute(
                "SELECT user_id, device_id, count(*) FROM auth.refresh_tokens "
                "WHERE revoked = FALSE AND session_id IS NULL "
                "GROUP BY user_id, device_id ORDER BY count(*) DESC")
            groups = cur.fetchall()

        ungroupable = [(uid, cnt) for uid, dev, cnt in groups if not dev]
        groupable = [(uid, dev, cnt) for uid, dev, cnt in groups if dev]

        print(f"[backfill] 存量未吊销且无 session_id 的 refresh token：{total} 行")
        print(f"[backfill] 可归组 (user_id, device_id) 组数：{len(groupable)}；"
              f"涉及行数：{sum(c for *_, c in groupable)}")
        print(f"[backfill] 无法归组（device_id 为空）行数：{sum(c for _, c in ungroupable)}")

        if not args.execute:
            print("[backfill] dry-run 结束，未写库")
            return

        with conn.cursor() as cur:
            # 可归组：每组补建一个 session（created_at 取该组最早行，刷新过期取组内最新）
            for uid, dev, _cnt in groupable:
                cur.execute(
                    "INSERT INTO auth.sessions (user_id, device_id, refresh_expires_at, created_at, "
                    "last_active_at) "
                    "SELECT %s, %s, max(expires_at), min(created_at), max(created_at) "
                    "FROM auth.refresh_tokens "
                    "WHERE user_id = %s AND device_id = %s AND revoked = FALSE "
                    "AND session_id IS NULL RETURNING id", (uid, dev, uid, dev))
                sid, = cur.fetchone()
                cur.execute(
                    "UPDATE auth.refresh_tokens SET session_id = %s "
                    "WHERE user_id = %s AND device_id = %s AND revoked = FALSE "
                    "AND session_id IS NULL", (sid, uid, dev))
                print(f"[backfill] user={uid} device={dev!r} → session {sid}")
            # 无法归组：吊销（安全优先；用户下次刷新 401 后重新登录）
            cur.execute(
                "UPDATE auth.refresh_tokens SET revoked = TRUE, revoked_at = now() "
                "WHERE revoked = FALSE AND session_id IS NULL AND device_id = ''")
            print(f"[backfill] 无法归组行已吊销：{cur.rowcount}")
            cur.execute(
                "UPDATE auth.refresh_tokens SET revoked = TRUE, revoked_at = now() "
                "WHERE revoked = FALSE AND session_id IS NULL")
            cur.execute(
                "SELECT count(*) FROM auth.refresh_tokens "
                "WHERE revoked = FALSE AND session_id IS NULL")
            residual, = cur.fetchone()
        conn.commit()
        print(f"[backfill] 执行完成，残余未归组未吊销行：{residual}（验收标准 S10 要求为 0）")


if __name__ == "__main__":
    main()
