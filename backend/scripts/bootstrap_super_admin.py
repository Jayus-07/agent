"""受控提升既有用户为 super_admin 的运维入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import text

from backend.memory.database import get_session
from backend.security.session_service import SessionRef, SessionService
from backend.shared.logger import logger


class BootstrapError(RuntimeError):
    """表示可预期的 bootstrap 业务失败。"""


@dataclass(frozen=True)
class BootstrapResult:
    """一次成功 bootstrap 的最小审计输出。"""

    user_id: int
    tenant_id: str
    revoked_sessions: list[SessionRef]
    changed: bool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将指定租户中的既有用户提升为 super_admin",
    )
    parser.add_argument("--tenant", required=True, help="目标租户 ID")
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--username", help="既有用户名")
    selectors.add_argument("--user-id", type=int, help="既有用户 ID")
    return parser


async def bootstrap_super_admin(
    *,
    tenant_id: str,
    username: str | None,
    user_id: int | None,
    session_service: SessionService | None = None,
) -> BootstrapResult:
    """在单一事务内提升用户、记审计并吊销其所有旧会话。"""
    service = session_service or SessionService()
    selector_sql = "username = :username" if username is not None else "id = :user_id"
    selector_params = {"username": username} if username is not None else {"user_id": user_id}

    async for db in get_session():
        row = (await db.execute(text(
            "SELECT id, username, role FROM auth.users "
            "WHERE tenant_id = :tenant_id AND " + selector_sql + " FOR UPDATE"
        ), {"tenant_id": tenant_id, **selector_params})).mappings().first()
        if row is None:
            await db.rollback()
            raise BootstrapError("指定租户中不存在目标用户")

        target_id = int(row["id"])
        old_role = str(row["role"])
        if old_role == "super_admin":
            await db.rollback()
            return BootstrapResult(
                user_id=target_id,
                tenant_id=tenant_id,
                revoked_sessions=[],
                changed=False,
            )

        await db.execute(text(
            "UPDATE auth.users SET role = 'super_admin', version = version + 1, "
            "updated_at = now() WHERE id = :user_id AND tenant_id = :tenant_id"
        ), {"user_id": target_id, "tenant_id": tenant_id})
        await db.execute(text(
            "INSERT INTO auth.rbac_audits "
            "(tenant_id, actor_user_id, target_user_id, action, before_state, "
            "after_state, result) VALUES "
            "(:tenant_id, NULL, :target_user_id, 'user.bootstrap_super_admin', "
            "CAST(:before_state AS JSONB), CAST(:after_state AS JSONB), 'success')"
        ), {
            "tenant_id": tenant_id,
            "target_user_id": target_id,
            "before_state": json.dumps({"platformRole": old_role}),
            "after_state": json.dumps({"platformRole": "super_admin"}),
        })
        refs = await service.revoke_user_sessions(
            db,
            target_id,
            reason="bootstrap_super_admin",
        )
        await db.commit()
        break
    else:  # pragma: no cover - get_session 必须至少产生一个数据库会话。
        raise BootstrapError("数据库会话不可用")

    service.clear_redis_for_sessions(refs)
    return BootstrapResult(
        user_id=target_id,
        tenant_id=tenant_id,
        revoked_sessions=refs,
        changed=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """解析显式租户/用户选择器并返回进程退出码。"""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    tenant_id = args.tenant.strip()
    username = args.username.strip() if args.username else None
    if not tenant_id or (username is not None and not username):
        print("tenant 与 username 不能为空", file=sys.stderr)
        return 2

    try:
        result = asyncio.run(bootstrap_super_admin(
            tenant_id=tenant_id,
            username=username,
            user_id=args.user_id,
        ))
    except BootstrapError as exc:
        logger.error("[bootstrap-super-admin] 失败: %s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        logger.exception("[bootstrap-super-admin] 未预期失败")
        return 1

    action = "已提升" if result.changed else "已是"
    print(
        f"{action} super_admin: user_id={result.user_id} "
        f"tenant={result.tenant_id} revoked_sessions={len(result.revoked_sessions)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
