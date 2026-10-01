"""为 Prompt 评测准备测试数据库中的模型绑定。

脚本只接受显式的测试数据库配置，拒绝生产主机/生产库名，且不会打印 API Key。
"""
from __future__ import annotations

import argparse
import asyncio
import json
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


def build_seed_configs() -> dict[str, dict[str, object]]:
    """读取 CI 评测所需的聊天模型和 embedding 模型配置。

    RAG 评测不仅需要生成模型，还需要能访问向量索引的 embedding 模型；
    两者都必须显式配置，避免 GitHub 干净数据库里退回到不存在的本地模型。
    """
    names = {
        "eval_gen": (
            "PROMPT_EVAL_TEST_PROVIDER_ID",
            "PROMPT_EVAL_TEST_MODEL",
            "PROMPT_EVAL_TEST_BASE_URL",
            "PROMPT_EVAL_TEST_API_KEY",
        ),
        "embedding": (
            "PROMPT_EVAL_TEST_EMBEDDING_PROVIDER_ID",
            "PROMPT_EVAL_TEST_EMBEDDING_MODEL",
            "PROMPT_EVAL_TEST_EMBEDDING_BASE_URL",
            "PROMPT_EVAL_TEST_EMBEDDING_API_KEY",
        ),
    }
    configs: dict[str, dict[str, object]] = {}
    for role, env_names in names.items():
        provider_id, model_name, base_url, api_key = (
            os.getenv(name, "").strip() for name in env_names
        )
        if not provider_id or not model_name or not base_url or not api_key:
            raise RuntimeError(
                "必须设置测试模型配置: " + "/".join(env_names)
            )
        configs[role] = {
            "provider_id": provider_id,
            "model_name": model_name,
            "base_url": base_url,
            "api_key": api_key,
        }
    configs["embedding"].update({"adapter": "openai_embedding", "dimensions": 1024})
    return configs


async def seed(*, dry_run: bool = False) -> None:
    host, database = _require_test_database()
    configs = build_seed_configs()
    chat = configs["eval_gen"]
    embedding = configs["embedding"]

    print(f"目标测试库: {host}/{database}")
    print(
        "准备绑定: "
        f"chat={chat['provider_id']}/{chat['model_name']}, "
        f"embedding={embedding['provider_id']}/{embedding['model_name']}"
    )
    if dry_run:
        return

    # 仅在安全检查完成后设置 DB 配置，再惰性导入会话模块。
    os.environ["MEMORY_PGDATABASE"] = database
    from backend.memory.database import get_session
    from backend.shared.crypto import encrypt_secret, fingerprint, last4

    async for session in get_session():
        for role, config in (("eval_gen", chat), ("embedding", embedding)):
            provider_id = str(config["provider_id"])
            model_name = str(config["model_name"])
            base_url = str(config["base_url"])
            api_key = str(config["api_key"])
            driver = "specialized" if role == "embedding" else "openai"
            model_kind = "embedding" if role == "embedding" else "chat"
            await session.execute(
                text(
                    """
                    INSERT INTO llm_providers
                        (id, display_name, driver, base_url, billing, is_builtin,
                         enabled, created_by, updated_at)
                    VALUES (:id, :display_name, :driver, :base_url, 'metered', false,
                            true, 'prompt-eval-seed', now())
                    ON CONFLICT (id) DO UPDATE SET
                        driver = EXCLUDED.driver, base_url = EXCLUDED.base_url,
                        enabled = true, updated_at = now()
                    """
                ),
                {
                    "id": provider_id,
                    "display_name": provider_id,
                    "driver": driver,
                    "base_url": base_url,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO llm_models
                        (name, provider_id, display_name, model_kind, source, created_by)
                    VALUES (:name, :provider_id, :display_name, :model_kind, 'user',
                            'prompt-eval-seed')
                    ON CONFLICT (name) DO UPDATE SET
                        provider_id = EXCLUDED.provider_id, model_kind = EXCLUDED.model_kind,
                        enabled = true, updated_at = now()
                    """
                ),
                {
                    "name": model_name,
                    "provider_id": provider_id,
                    "display_name": model_name,
                    "model_kind": model_kind,
                },
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
            {"model_name": str(chat["model_name"])},
        )
        await session.execute(
            text(
                """
                INSERT INTO llm_model_role_bindings (role, model_name, updated_by, updated_at)
                VALUES ('embedding', :model_name, 'prompt-eval-seed', now())
                ON CONFLICT (role) DO UPDATE SET
                    model_name = EXCLUDED.model_name, updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """
            ),
            {"model_name": str(embedding["model_name"])},
        )
        await session.execute(
            text(
                """
                INSERT INTO llm_specialized_model_bindings
                    (role, provider_id, adapter, model_name, base_url, options,
                     enabled, last_probe_ok, last_probe_summary, updated_by, updated_at)
                VALUES ('embedding', :provider_id, :adapter, :model_name, :base_url,
                        CAST(:options AS jsonb), true, NULL, '由 Prompt 评测 CI 写入',
                        'prompt-eval-seed', now())
                ON CONFLICT (role) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id, adapter = EXCLUDED.adapter,
                    model_name = EXCLUDED.model_name, base_url = EXCLUDED.base_url,
                    options = EXCLUDED.options, enabled = true,
                    last_probe_ok = NULL, last_probe_summary = EXCLUDED.last_probe_summary,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """
            ),
            {
                "provider_id": str(embedding["provider_id"]),
                "adapter": str(embedding["adapter"]),
                "model_name": str(embedding["model_name"]),
                "base_url": str(embedding["base_url"]),
                "options": json.dumps(
                    {"dimensions": int(embedding["dimensions"])},
                    ensure_ascii=False,
                ),
            },
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
