"""把旧模型环境变量一次性迁移到数据库注册表。

该脚本只用于本次从 env 到 DB 的割接，设计为幂等：

* 已存在的数据库凭据不覆盖，避免把管理端刚保存的 Key 改回旧值；
* 已存在的角色绑定不覆盖，数据库配置优先；
* 仅把旧 env 中的模型供应商配置写入加密凭据表，不打印 Key、密文或完整 URL；
* ``--dry-run`` 只输出将要迁移的供应商/角色名称，不执行写入。

运行方式（容器内）：
    python -m backend.scripts.migrate_model_env_to_db --dry-run
    python -m backend.scripts.migrate_model_env_to_db
"""
from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass

from sqlalchemy import text

from backend.memory.database import get_session
from backend.shared.crypto import encrypt_secret, fingerprint, last4


@dataclass(frozen=True)
class ProviderEnvSpec:
    key_env: str
    base_env: str
    default_base_url: str


# 只迁移通用文本模型供应商。embedding/rerank/OCR 的旧变量不能覆盖
# 管理端已经分别登记的专项模型与 Key。
PROVIDER_ENV: dict[str, ProviderEnvSpec] = {
    "deepseek": ProviderEnvSpec(
        "DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"
    ),
    "minimax": ProviderEnvSpec(
        "MINIMAX_API_KEY", "MINIMAX_API_BASE", "https://api.minimaxi.com/anthropic"
    ),
    "qwen": ProviderEnvSpec(
        "QWEN_API_KEY",
        "QWEN_API_BASE",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "qwen_tp": ProviderEnvSpec(
        "QWEN_TP_API_KEY",
        "QWEN_TP_API_BASE",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    ),
    "siliconflow": ProviderEnvSpec(
        "SILICONFLOW_API_KEY", "SILICONFLOW_API_BASE", "https://api.siliconflow.cn/v1"
    ),
    "vllm": ProviderEnvSpec(
        "VLLM_API_KEY", "VLLM_API_BASE", "http://localhost:8000/v1"
    ),
}

ROLE_ENV = {
    "main": "LLM_MODEL",
    "doc": "DOC_LLM_MODEL",
    "tool_selector": "TOOL_SELECTOR_MODEL",
    "fallback": "LLM_FALLBACK_MODEL",
    "eval_gen": "EVAL_GEN_MODEL",
}


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


async def migrate(*, dry_run: bool) -> None:
    provider_actions: list[str] = []
    role_actions: list[str] = []
    session_found = False

    async for session in get_session():
        session_found = True
        provider_rows = (
            await session.execute(
                text("SELECT id, base_url FROM llm_providers FOR UPDATE")
            )
        ).mappings().all()
        provider_by_id = {str(row["id"]): row for row in provider_rows}

        credential_rows = (
            await session.execute(
                text("SELECT provider_id FROM llm_provider_credentials")
            )
        ).mappings().all()
        credential_provider_ids = {
            str(row["provider_id"]) for row in credential_rows
        }

        for provider_id, spec in PROVIDER_ENV.items():
            key = _env(spec.key_env)
            if not key or provider_id not in provider_by_id:
                continue

            base_url = _env(spec.base_env) or spec.default_base_url
            current_base_url = str(provider_by_id[provider_id]["base_url"] or "").strip()
            if not current_base_url and base_url:
                provider_actions.append(f"provider:{provider_id}:base_url")
                if not dry_run:
                    await session.execute(
                        text(
                            """
                            UPDATE llm_providers
                               SET base_url = :base_url,
                                   updated_at = now()
                             WHERE id = :provider_id
                            """
                        ),
                        {
                            "base_url": base_url,
                            "provider_id": provider_id,
                        },
                    )

            if provider_id in credential_provider_ids:
                continue

            provider_actions.append(f"credential:{provider_id}")
            if not dry_run:
                await session.execute(
                    text(
                        """
                        INSERT INTO llm_provider_credentials
                            (provider_id, key_cipher, key_fingerprint, key_last4,
                             key_version, updated_by, updated_at)
                        VALUES
                            (:provider_id, :key_cipher, :key_fingerprint, :key_last4,
                             1, :updated_by, now())
                        ON CONFLICT (provider_id) DO NOTHING
                        """
                    ),
                    {
                        "provider_id": provider_id,
                        "key_cipher": encrypt_secret(key),
                        "key_fingerprint": fingerprint(key),
                        "key_last4": last4(key),
                        "updated_by": "env-migration-20260920",
                    },
                )

        role_rows = (
            await session.execute(
                text("SELECT role FROM llm_model_role_bindings FOR UPDATE")
            )
        ).mappings().all()
        existing_roles = {str(row["role"]) for row in role_rows}
        for role, env_name in ROLE_ENV.items():
            value = _env(env_name)
            if not value or role in existing_roles:
                continue
            role_actions.append(f"role:{role}")
            if not dry_run:
                await session.execute(
                    text(
                        """
                        INSERT INTO llm_model_role_bindings
                            (role, model_name, updated_by, updated_at)
                        VALUES (:role, :model_name, :updated_by, now())
                        ON CONFLICT (role) DO NOTHING
                        """
                    ),
                    {
                        "role": role,
                        "model_name": value,
                        "updated_by": "env-migration-20260920",
                    },
                )

        # 早期角色页曾把 rerank 模型名保存成 qwen3.7-text-reran，
        # 但目录中的真实模型是 qwen3.7-text-rerank。只在正确模型已登记时修复。
        typo_exists = (
            await session.execute(
                text(
                    """
                    SELECT 1
                      FROM llm_model_role_bindings
                     WHERE role = 'rerank'
                       AND model_name = 'qwen3.7-text-reran'
                       AND EXISTS (
                           SELECT 1 FROM llm_models
                            WHERE name = 'qwen3.7-text-rerank' AND enabled = true
                       )
                    """
                )
            )
        ).first()
        if typo_exists:
            role_actions.append("role:rerank:repair-typo")
            if not dry_run:
                await session.execute(
                    text(
                        """
                        UPDATE llm_model_role_bindings
                           SET model_name = 'qwen3.7-text-rerank',
                               updated_by = 'env-migration-20260920',
                               updated_at = now()
                         WHERE role = 'rerank'
                           AND model_name = 'qwen3.7-text-reran'
                        """
                    )
                )
    if not session_found:
        raise RuntimeError("get_session 未产出数据库会话")

    action_label = "计划迁移" if dry_run else "已迁移"
    print(f"{action_label}供应商动作: {', '.join(provider_actions) or '无'}")
    print(f"{action_label}角色动作: {', '.join(role_actions) or '无'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移模型 env 配置到数据库")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写数据库")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
