"""为 Prompt 评测准备测试数据库中的模型绑定。

脚本只接受显式的测试数据库配置，拒绝生产主机/生产库名，且不会打印 API Key。
"""
from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import text


def _require_test_database() -> tuple[str, str]:
    host = os.getenv("PGHOST", "localhost").strip().lower()
    database = os.getenv("PROMPT_EVAL_TEST_PGDATABASE", "").strip()
    if not database:
        raise RuntimeError("必须设置 PROMPT_EVAL_TEST_PGDATABASE，禁止使用默认生产库")
    if host not in {"localhost", "127.0.0.1", "::1"} and "test" not in host:
        raise RuntimeError(f"拒绝非测试数据库主机: {host}")
    if "test" not in database.lower() and os.getenv("PROMPT_EVAL_ALLOW_NON_TEST_DB") != "1":
        raise RuntimeError(f"数据库名看起来不是测试库: {database}")
    return host, database


async def seed(*, dry_run: bool = False) -> None:
    host, database = _require_test_database()
    provider_id = os.getenv("PROMPT_EVAL_TEST_PROVIDER_ID", "prompt-eval-test").strip()
    model_name = os.getenv("PROMPT_EVAL_TEST_MODEL", "prompt-eval-model").strip()
    base_url = os.getenv("PROMPT_EVAL_TEST_BASE_URL", "").strip()
    api_key = os.getenv("PROMPT_EVAL_TEST_API_KEY", "").strip()
    if not provider_id or not model_name or not base_url or not api_key:
        raise RuntimeError(
            "必须设置 PROMPT_EVAL_TEST_PROVIDER_ID/TEST_MODEL/TEST_BASE_URL/TEST_API_KEY"
        )

    print(f"目标测试库: {host}/{database}")
    print(f"准备绑定: provider={provider_id}, model={model_name}, role=eval_gen")
    if dry_run:
        return

    # 仅在安全检查完成后设置 DB 配置，再惰性导入会话模块。
    os.environ["MEMORY_PGDATABASE"] = database
    from backend.memory.database import get_session
    from backend.shared.crypto import encrypt_secret, fingerprint, last4

    async for session in get_session():
        await session.execute(
            text(
                """
                INSERT INTO llm_providers
                    (id, display_name, driver, base_url, billing, is_builtin,
                     enabled, created_by, updated_at)
                VALUES (:id, :display_name, 'openai', :base_url, 'metered', false,
                        true, 'prompt-eval-seed', now())
                ON CONFLICT (id) DO UPDATE SET
                    base_url = EXCLUDED.base_url, enabled = true, updated_at = now()
                """
            ),
            {"id": provider_id, "display_name": provider_id, "base_url": base_url},
        )
        await session.execute(
            text(
                """
                INSERT INTO llm_models
                    (name, provider_id, display_name, model_kind, source, created_by)
                VALUES (:name, :provider_id, :display_name, 'chat', 'user',
                        'prompt-eval-seed')
                ON CONFLICT (name) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id, enabled = true,
                    updated_at = now()
                """
            ),
            {"name": model_name, "provider_id": provider_id, "display_name": model_name},
        )
        await session.execute(
            text(
                """
                INSERT INTO llm_provider_credentials
                    (provider_id, key_cipher, key_fingerprint, key_last4,
                     key_version, updated_by, updated_at)
                VALUES (:provider_id, :key_cipher, :key_fingerprint, :key_last4,
                        1, 'prompt-eval-seed', now())
                ON CONFLICT (provider_id) DO UPDATE SET
                    key_cipher = EXCLUDED.key_cipher,
                    key_fingerprint = EXCLUDED.key_fingerprint,
                    key_last4 = EXCLUDED.key_last4,
                    key_version = llm_provider_credentials.key_version + 1,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """
            ),
            {
                "provider_id": provider_id,
                "key_cipher": encrypt_secret(api_key),
                "key_fingerprint": fingerprint(api_key),
                "key_last4": last4(api_key),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO llm_model_role_bindings (role, model_name, updated_by, updated_at)
                VALUES ('eval_gen', :model_name, 'prompt-eval-seed', now())
                ON CONFLICT (role) DO UPDATE SET
                    model_name = EXCLUDED.model_name, updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """
            ),
            {"model_name": model_name},
        )
        break
    else:
        raise RuntimeError("未能建立测试数据库会话")
    print("测试评测模型绑定已写入（Key 仅保存加密值）")


def main() -> None:
    parser = argparse.ArgumentParser(description="写入 Prompt 评测测试模型绑定")
    parser.add_argument("--dry-run", action="store_true", help="只校验配置，不写数据库")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
