"""在生产服务切换前验证数据库中的供应商凭据能被当前主钥匙解开。"""
from __future__ import annotations

import asyncio
import os
import re
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from backend.memory.database import get_session


def unreadable_provider_ids(rows: list[dict], key: str) -> list[str]:
    """返回无法由当前主钥匙解密的供应商 ID，不读取或输出密钥明文。"""
    fernet = Fernet(key.encode("ascii"))
    failed: list[str] = []
    for row in rows:
        value = str(row.get("key_cipher") or "")
        if not value.startswith("enc:"):
            continue
        try:
            fernet.decrypt(value[len("enc:"):].encode("ascii"))
        except (InvalidToken, UnicodeEncodeError, ValueError):
            provider_id = str(row.get("provider_id") or "unknown")
            failed.append(re.sub(r"[^A-Za-z0-9_.-]", "?", provider_id)[:80])
    return failed


async def _load_provider_credentials() -> list[dict]:
    async for session in get_session():
        result = await session.execute(text(
            "SELECT provider_id, key_cipher FROM llm_provider_credentials"
        ))
        return [dict(row) for row in result.mappings().all()]
    return []


def main() -> int:
    key = os.getenv("SECRETS_ENCRYPTION_KEY", "").strip()
    try:
        Fernet(key.encode("ascii"))
    except (TypeError, UnicodeEncodeError, ValueError):
        print("[secrets preflight] encryption key is missing or invalid", file=sys.stderr)
        return 1

    try:
        rows = asyncio.run(_load_provider_credentials())
    except Exception as exc:  # noqa: BLE001 — 只输出异常类型，避免带出连接配置
        print(
            f"[secrets preflight] provider credential query failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    if not rows:
        print("[secrets preflight] no stored provider credentials to verify")
        return 0

    failed = unreadable_provider_ids(rows, key)
    if failed:
        shown = ", ".join(failed[:20])
        suffix = " …" if len(failed) > 20 else ""
        print(
            f"[secrets preflight] {len(failed)}/{len(rows)} provider credentials "
            f"cannot be decrypted with the configured key: {shown}{suffix}",
            file=sys.stderr,
        )
        return 1

    print(f"[secrets preflight] all {len(rows)} provider credentials are decryptable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
