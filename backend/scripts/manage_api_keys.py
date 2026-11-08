"""manage_api_keys.py — 调用方级 API Key 管理（2026-11-08 安全审计 T9）。

用法（项目根目录）::

    python -m backend.scripts.manage_api_keys create --caller cs-dispatcher --expires-days 90
    python -m backend.scripts.manage_api_keys list
    python -m backend.scripts.manage_api_keys revoke --key-id ak_xxxxxxxx --reason leak

说明：
- 明文只在 create 时输出一次，库中仅存 SHA-256 摘要；
- 应用侧有 15s 进程缓存，停用/吊销最迟 15s 生效；
- env 的 ``API_KEY`` 仍是 legacy/引导凭据，不受本脚本管理。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from backend.config.database import MEMORY_DB_CONFIG
from backend.security.api_keys import generate_api_key, hash_api_key


def _connect():
    import psycopg2

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=5)


def cmd_create(args: argparse.Namespace) -> int:
    raw, key_id, key_hash = generate_api_key()
    expires = None
    if args.expires_days:
        expires = datetime.now(timezone.utc) + timedelta(days=args.expires_days)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.api_keys "
                "(key_id, key_hash, caller, tenant_id, expires_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (key_id, key_hash, args.caller, args.tenant, expires),
            )
        conn.commit()
    print(f"key_id={key_id}")
    print(f"caller={args.caller} tenant={args.tenant or '-'} "
          f"expires={expires.isoformat() if expires else 'never'}")
    print(f"api_key={raw}")
    print("只显示一次；请立即写入调用方的密钥管理，不要提交到仓库或贴进日志。")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key_id, caller, tenant_id, enabled, created_at, "
                "expires_at, last_used_at, revoked_at FROM auth.api_keys "
                "ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
    if not rows:
        print("(no api keys)")
        return 0
    for key_id, caller, tenant, enabled, created, expires, used, revoked in rows:
        state = "revoked" if revoked else ("active" if enabled else "disabled")
        print(f"{key_id}\t{state}\tcaller={caller}\ttenant={tenant or '-'}\t"
              f"created={created.isoformat()}\texpires="
              f"{expires.isoformat() if expires else '-'}\tlast_used="
              f"{used.isoformat() if used else '-'}")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth.api_keys SET enabled = FALSE, revoked_at = now(), "
                "revoked_reason = %s WHERE key_id = %s AND revoked_at IS NULL",
                (args.reason, args.key_id),
            )
            updated = cur.rowcount
        conn.commit()
    if not updated:
        print(f"key_id={args.key_id} 不存在或已吊销", file=sys.stderr)
        return 1
    print(f"revoked key_id={args.key_id} reason={args.reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建调用方 Key（明文只输出一次）")
    create.add_argument("--caller", required=True, help="调用方名称/归属")
    create.add_argument("--tenant", default=None, help="可选租户绑定")
    create.add_argument("--expires-days", type=int, default=None,
                        help="有效期天数（缺省不过期）")
    create.set_defaults(func=cmd_create)

    listing = sub.add_parser("list", help="列出 Key（不含明文/摘要）")
    listing.set_defaults(func=cmd_list)

    revoke = sub.add_parser("revoke", help="吊销 Key")
    revoke.add_argument("--key-id", required=True)
    revoke.add_argument("--reason", default="manual")
    revoke.set_defaults(func=cmd_revoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
