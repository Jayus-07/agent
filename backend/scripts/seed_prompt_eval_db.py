"""为 Prompt 评测准备测试数据库中的模型绑定。

脚本只接受显式的测试数据库配置，拒绝生产主机/生产库名，且不会打印 API Key。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

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
    configs["embedding"].update({"adapter": "openai_embedding"})
    # 维度不写死：以 binding 声明 + 评测前 API 探针实测双重校验
    #（scripts/seed_eval_model_bindings.py --probe）。默认 1024 与存量向量兼容。
    configs["embedding"]["dimensions"] = int(
        os.getenv("PROMPT_EVAL_TEST_EMBEDDING_DIMENSIONS", "1024")
    )
    # rerank 可选：RAG 真实检索评测必需（cloud 口径无 binding 会退回本地
    # reranker，违反「评测统一 DB API 模型」）；prompt_eval 场景可不配。
    rerank_env = (
        "PROMPT_EVAL_TEST_RERANK_PROVIDER_ID",
        "PROMPT_EVAL_TEST_RERANK_MODEL",
        "PROMPT_EVAL_TEST_RERANK_BASE_URL",
        "PROMPT_EVAL_TEST_RERANK_API_KEY",
    )
    rerank_vals = tuple(os.getenv(n, "").strip() for n in rerank_env)
    if all(rerank_vals):
        configs["rerank"] = {
            "provider_id": rerank_vals[0],
            "model_name": rerank_vals[1],
            "base_url": rerank_vals[2],
            "api_key": rerank_vals[3],
            "adapter": os.getenv(
                "PROMPT_EVAL_TEST_RERANK_ADAPTER", "dashscope_rerank"
            ),
        }
    elif any(rerank_vals):
        raise RuntimeError("rerank 配置不完整: " + "/".join(rerank_env))
    else:
        print("未配置 rerank 绑定（RAG 检索评测必须显式提供，禁止回退本地模型）")
    return configs


def build_price_snapshot(configs: dict) -> list[dict]:
    """只接收源 Registry 已批准的价格快照，保留版本、来源和双人审查。"""
    raw = os.getenv("PROMPT_EVAL_APPROVED_PRICES_JSON", "").strip()
    if not raw:
        return []  # 后续价格预检会在任何付费调用之前拒绝缺价。
    rows = json.loads(raw)
    if not isinstance(rows, list) or not rows:
        raise ValueError("已批准价格快照必须是非空列表")
    required = {
        (str(config["model_name"]), "llm" if role == "eval_gen" else role)
        for role, config in configs.items()
    }
    now = datetime.now(timezone.utc)
    seen = set()
    currencies: dict[tuple[str, str], str] = {}
    for row in rows:
        if not isinstance(row, dict) or (row.get("model_name"), row.get("component")) not in required:
            raise ValueError("价格快照包含未绑定模型")
        key = (row["model_name"], row["component"], row.get("dimension"))
        if key in seen:
            raise ValueError("价格快照包含重复计价维度")
        seen.add(key)
        if (
            row.get("approval_status") != "approved"
            or not str(row.get("reviewer_1") or "").strip()
            or not str(row.get("reviewer_2") or "").strip()
            or row["reviewer_1"] == row["reviewer_2"]
            or not str(row.get("source") or "").strip()
            or not str(row.get("price_table_version") or "").strip()
        ):
            raise ValueError("价格快照缺少原始批准、双人审查、来源或版本")
        effective = datetime.fromisoformat(str(row.get("effective_from", "")).replace("Z", "+00:00"))
        expires = row.get("effective_to")
        if effective.tzinfo is None or effective > now:
            raise ValueError("价格快照尚未生效或缺少时区")
        if expires:
            end = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if end.tzinfo is None or end <= now:
                raise ValueError("价格快照已失效或缺少时区")
        row["effective_from"] = effective
        row["effective_to"] = end if expires else None
        row["price_per_unit"] = Decimal(str(row.get("price_per_unit")))
        if row.get("unit") not in {"per_1m_tokens", "per_call"} or row.get("currency") not in {"CNY", "USD"}:
            raise ValueError("价格快照的单位或币种不受支持")
        dimension = str(row.get("dimension") or "")
        unit = row["unit"]
        if dimension == "tool_call" and unit != "per_call":
            raise ValueError("tool_call 维度必须使用 per_call")
        if dimension != "tool_call" and unit == "per_call":
            raise ValueError("Token 维度必须使用 per_1m_tokens")
        currency_key = (str(row["model_name"]), str(row["component"]))
        existing_currency = currencies.setdefault(currency_key, row["currency"])
        if existing_currency != row["currency"]:
            raise ValueError("同一模型和组件的价格快照不能混用币种")
    from backend.infra.llm.pricing import PriceTable

    table = PriceTable.from_rows(rows)
    for model, component in required:
        table.require(model, component, enforce=True)
    return rows


async def seed(*, dry_run: bool = False) -> None:
    host, database = _require_test_database()
    configs = build_seed_configs()
    price_snapshot = build_price_snapshot(configs)
    chat = configs["eval_gen"]
    embedding = configs["embedding"]

    print(f"目标测试库: {host}/{database}")
    print(
        "准备绑定: "
        f"chat={chat['provider_id']}/{chat['model_name']}, "
        f"embedding={embedding['provider_id']}/{embedding['model_name']}"
        + (
            f", rerank={configs['rerank']['provider_id']}/{configs['rerank']['model_name']}"
            if "rerank" in configs
            else ""
        )
    )
    if dry_run:
        return

    # 仅在安全检查完成后设置 DB 配置，再惰性导入会话模块。
    os.environ["MEMORY_PGDATABASE"] = database
    from backend.memory.database import get_session
    from backend.shared.crypto import encrypt_secret, fingerprint, last4

    cred_roles: list[tuple[str, dict[str, object]]] = [
        ("eval_gen", chat), ("embedding", embedding)
    ]
    if "rerank" in configs:
        cred_roles.append(("rerank", configs["rerank"]))

    async for session in get_session():
        for role, config in cred_roles:
            provider_id = str(config["provider_id"])
            model_name = str(config["model_name"])
            base_url = str(config["base_url"])
            api_key = str(config["api_key"])
            driver = "specialized" if role in ("embedding", "rerank") else "openai"
            model_kind = "embedding" if role == "embedding" else (
                "rerank" if role == "rerank" else "chat"
            )
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
        role_bindings = [("eval_gen", chat), ("embedding", embedding)]
        if "rerank" in configs:
            role_bindings.append(("rerank", configs["rerank"]))
        for role, config in role_bindings:
            await session.execute(
                text(
                    """
                    INSERT INTO llm_model_role_bindings
                        (role, model_name, updated_by, updated_at)
                    VALUES (:role, :model_name, 'prompt-eval-seed', now())
                    ON CONFLICT (role) DO UPDATE SET
                        model_name = EXCLUDED.model_name,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """
                ),
                {"role": role, "model_name": str(config["model_name"])},
            )
        # specialized 通道绑定（embedding 必配；rerank 可选但 RAG 检索评测必需）
        for role, config in (
            (role, configs[role])
            for role in ("embedding", "rerank") if role in configs
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO llm_specialized_model_bindings
                        (role, provider_id, adapter, model_name, base_url, options,
                         enabled, last_probe_ok, last_probe_summary, updated_by, updated_at)
                    VALUES (:role, :provider_id, :adapter, :model_name, :base_url,
                            CAST(:options AS jsonb), true, NULL,
                            '由 Prompt 评测 CI 写入', 'prompt-eval-seed', now())
                    ON CONFLICT (role) DO UPDATE SET
                        provider_id = EXCLUDED.provider_id, adapter = EXCLUDED.adapter,
                        model_name = EXCLUDED.model_name, base_url = EXCLUDED.base_url,
                        options = EXCLUDED.options, enabled = true,
                        last_probe_ok = NULL,
                        last_probe_summary = EXCLUDED.last_probe_summary,
                        updated_by = EXCLUDED.updated_by, updated_at = now()
                    """
                ),
                {
                    "role": role,
                    "provider_id": str(config["provider_id"]),
                    "adapter": str(config["adapter"]),
                    "model_name": str(config["model_name"]),
                    "base_url": str(config["base_url"]),
                    "options": json.dumps(
                        (
                            {"dimensions": int(config["dimensions"])}
                            if "dimensions" in config
                            else {}
                        ),
                        ensure_ascii=False,
                    ),
                },
            )
        # 只向已校验的隔离库复制已批准快照，不创建新审批或回写源价格。
        if price_snapshot:
            # 快照导入串行化；相同版本不同价格必须报错，不能静默覆盖或跳过。
            await session.execute(text("LOCK TABLE model_price IN SHARE ROW EXCLUSIVE MODE"))
        for row in price_snapshot:
            result = await session.execute(text("""
                SELECT price_per_unit, unit, currency, source, approval_status,
                    reviewer_1, reviewer_2, effective_to
                FROM model_price WHERE model_name=:model_name
                AND component=:component AND dimension=:dimension
                AND price_table_version=:price_table_version
                AND effective_from=CAST(:effective_from AS timestamptz)
            """), row)
            existing = result.mappings().first()
            if existing is not None:
                if any(existing[key] != row.get(key) for key in existing):
                    raise ValueError("价格快照与目标库同版本记录冲突，拒绝写入")
                continue
            await session.execute(text("""
                INSERT INTO model_price (
                    model_name, component, dimension, price_per_unit, unit, currency,
                    price_table_version, source, effective_from, effective_to,
                    approval_status, reviewer_1, reviewer_2
                )
                SELECT :model_name, :component, :dimension, :price_per_unit, :unit,
                    :currency, :price_table_version, :source,
                    CAST(:effective_from AS timestamptz), CAST(:effective_to AS timestamptz),
                    'approved', :reviewer_1, :reviewer_2
                WHERE NOT EXISTS (
                    SELECT 1 FROM model_price WHERE model_name=:model_name
                    AND component=:component AND dimension=:dimension
                    AND price_table_version=:price_table_version
                    AND effective_from=CAST(:effective_from AS timestamptz)
                )
            """), {**row, "effective_to": row.get("effective_to")})
        # get_session 是 async generator；break 不会执行 yield 后的自动提交，
        # 必须在关闭会话前显式提交，确保 CI 后续评测进程能读到绑定。
        await session.commit()
        break
    else:
        raise RuntimeError("未能建立测试数据库会话")
    print("测试评测模型绑定已写入（Key 仅保存加密值）")


def verify_pricing() -> None:
    """在任何付费 Probe/索引前检查权威价格，禁止缺价跑完后再报未知。"""
    _host, database = _require_test_database()
    os.environ["MEMORY_PGDATABASE"] = database
    from backend.infra.llm.pricing import (
        NormalizedUsage,
        get_current_price_table,
        price_usage,
    )

    for role, config in build_seed_configs().items():
        component = "llm" if role == "eval_gen" else role
        model = str(config["model_name"])
        get_current_price_table(model, component).require(
            model, component, enforce=True,
        )
        result = price_usage(
            model, component,
            NormalizedUsage(input_tokens=1, output_tokens=int(component == "llm")),
            enforce=True,
        )
        if result.pricing_source != "approved_price_table" or result.cost_status != "exact":
            raise RuntimeError(f"评测价格或汇率未就绪: {model}/{component}")
    print("EVAL_PRICING_CHECK=passed（仅检查价格，不调用 Provider）")


def main() -> None:
    parser = argparse.ArgumentParser(description="写入 Prompt 评测测试模型绑定")
    parser.add_argument("--dry-run", action="store_true", help="只校验配置，不写数据库")
    parser.add_argument("--verify-pricing", action="store_true", help="只验证权威价格和汇率")
    args = parser.parse_args()
    if args.verify_pricing:
        verify_pricing()
    else:
        asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
