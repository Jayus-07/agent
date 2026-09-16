"""一次性迁移：把记忆库 "default" 桶的历史会话归属给真实用户。

背景（2026-09-16 记忆按登录用户隔离）：
  memory 路由此前接受 ?user_id= 自报且前端不传，所有登录用户的会话都落
  在 user_id="default" 桶。路由改为 resolve_identity 后，历史会话需要
  一次性划归给真实账号，否则登录后侧栏为空。

用法（仓库根，连接串走 .env 的 PGHOST/PGPASSWORD 等）：
  python scripts/assign_default_sessions.py --list              # 只看不动
  python scripts/assign_default_sessions.py --to 1              # 划归给 user 1
  python scripts/assign_default_sessions.py --to 1 --yes        # 免确认执行
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from backend.memory.database import AsyncSessionLocal


async def _main(to_user: str | None, assume_yes: bool) -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT session_id, user_id, title, updated_at "
            "FROM chat_sessions WHERE user_id = 'default' "
            "ORDER BY updated_at DESC"))).mappings().all()
        print(f"default 桶会话数: {len(rows)}")
        for r in rows[:20]:
            print(f"  {r['session_id'][:16]}…  {(r['title'] or '(无标题)')[:30]}  {r['updated_at']}")

        if to_user is None:
            print("\n(--list 只读模式) 用 --to <user_id> 执行划归")
            return

        users = (await db.execute(text(
            "SELECT id, username, role FROM auth.users ORDER BY id"))).mappings().all()
        print("\n现有用户:", ", ".join(f"{u['id']}={u['username']}({u['role']})" for u in users))
        if not any(str(u["id"]) == to_user for u in users):
            raise SystemExit(f"user_id={to_user} 不存在，先在上表里选一个")

        if not assume_yes:
            answer = input(f"\n把 {len(rows)} 条会话划归给 user {to_user}？[y/N] ")
            if answer.strip().lower() != "y":
                print("已取消")
                return

        result = await db.execute(text(
            "UPDATE chat_sessions SET user_id = :uid WHERE user_id = 'default'"),
            {"uid": to_user})
        await db.commit()
        print(f"已划归 {result.rowcount} 条会话 → user {to_user}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", help="目标 user_id（auth.users.id）")
    parser.add_argument("--list", action="store_true", help="只列出 default 桶内容")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    args = parser.parse_args()
    asyncio.run(_main(args.to if not args.list else None, args.yes))
