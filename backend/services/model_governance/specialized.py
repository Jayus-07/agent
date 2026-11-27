"""模型治理中心 - 专精模型（Embedding / Rerank 专用通道）配置。"""
from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Mapping
from sqlalchemy import text

from backend.infra.llm import registry_store as _real_registry_store
from backend.memory.database import get_session as _real_get_session
from backend.services import (
    specialized_model_adapters as _real_specialized_model_adapters,
    specialized_model_probe as _real_specialized_model_probe,
)
from backend.services.model_governance.base import (
    DependencyProxy,
    ModelConfigConflict,
    ModelConfigNotFound,
    _SPECIALIZED_ROLES,
    _iso,
    _provider_slug,
    _valid_base_url,
)
from backend.shared.crypto import (
    encrypt_secret as _real_encrypt_secret,
    last4 as _real_last4,
)

get_session = DependencyProxy("get_session", _real_get_session)
registry_store = DependencyProxy("registry_store", _real_registry_store)
specialized_model_adapters = DependencyProxy("specialized_model_adapters", _real_specialized_model_adapters)
specialized_model_probe = DependencyProxy("specialized_model_probe", _real_specialized_model_probe)
encrypt_secret = DependencyProxy("encrypt_secret", _real_encrypt_secret)
last4 = DependencyProxy("last4", _real_last4)


def _specialized_binding_payload(role: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """校验并归一化一条 embedding/rerank 绑定。"""
    if role not in _SPECIALIZED_ROLES:
        raise ValueError(f"不支持的专项模型角色：{role}")
    model_name = str(value.get("modelName") or value.get("model_name") or "").strip()
    if not model_name:
        raise ValueError(f"{role} 模型名不能为空")
    adapter = str(value.get("adapter") or "").strip()
    specialized_model_adapters.get_adapter(adapter)
    if role == "embedding" and not adapter.endswith("embedding"):
        raise ValueError("embedding 只能使用 embedding 适配器")
    if role == "rerank" and not adapter.endswith("rerank"):
        raise ValueError("rerank 只能使用 rerank 适配器")
    base_url = _valid_base_url(
        str(value.get("baseUrl") or value.get("base_url") or "")
    )
    if not base_url:
        raise ValueError(f"{role} 必须填写 Base URL")
    options = value.get("options") or {}
    if not isinstance(options, Mapping):
        raise ValueError(f"{role} options 必须是对象")
    return {
        "role": role,
        "model_name": model_name,
        "adapter": adapter,
        "base_url": base_url,
        "options": dict(options),
    }



class SpecializedMixin:
    """专项模型独立登记与通道绑定能力。"""

    async def configure_specialized(
        self,
        payload: Mapping[str, Any],
        operator: str,
    ) -> dict[str, Any]:
        """测试并保存 embedding/rerank 专项配置。

        所有网络测试在事务写入前完成；任一能力失败都只返回测试结果，不落库。
        """
        provider = payload.get("provider") or {}
        if not isinstance(provider, Mapping):
            raise ValueError("provider 必须是对象")
        display_name = str(provider.get("displayName") or provider.get("display_name") or "").strip()
        base_url = _valid_base_url(
            str(provider.get("baseUrl") or provider.get("base_url") or "")
        )
        if not display_name:
            raise ValueError("供应商名称不能为空")
        if not base_url:
            raise ValueError("供应商 Base URL 不能为空")
        provider_id = str(provider.get("providerId") or provider.get("provider_id") or "").strip()
        if provider_id:
            from backend.infra.llm.specialized import resolve_binding

            active_binding = resolve_binding("embedding")
            if active_binding and active_binding.provider_id == provider_id:
                raise ModelConfigConflict(
                    "该供应商正用于嵌入模型，旧专项入口不能覆盖其配置；请登记独立供应商后使用预检切换"
                )
        api_key_input = str(provider.get("apiKey") or provider.get("api_key") or "").strip()
        if not provider_id and not api_key_input:
            raise ValueError("新供应商必须填写 API Key")
        if provider_id and not api_key_input:
            api_key = await self._read_provider_secret(provider_id)
            if not api_key:
                raise ValueError("API Key 不能为空；编辑时可重新输入以轮换密钥")
        else:
            api_key = api_key_input

        raw_bindings = payload.get("bindings") or {}
        if not isinstance(raw_bindings, Mapping):
            raise ValueError("bindings 必须是对象")
        embedding_storage = None
        if "embedding" in raw_bindings:
            import asyncio

            from backend.rag.indexing.embedding_migration import storage_snapshot

            embedding_storage = await asyncio.to_thread(storage_snapshot)
            if embedding_storage["vectors"]:
                raise ModelConfigConflict(
                    "已有向量时不能通过旧专项接口切换嵌入配置，请使用角色绑定的预检、确认和重建流程"
                )
        bindings: list[dict[str, Any]] = []
        for role in ("embedding", "rerank"):
            raw = raw_bindings.get(role)
            if raw is None:
                continue
            if not isinstance(raw, Mapping):
                raise ValueError(f"{role} 配置必须是对象")
            bindings.append(_specialized_binding_payload(role, raw))
        if not bindings:
            raise ValueError("至少配置 embedding 或 rerank 之一")

        probe_results: list[dict[str, Any]] = []
        for binding in bindings:
            probe = await specialized_model_probe.probe_specialized(
                role=binding["role"],
                adapter=binding["adapter"],
                base_url=binding["base_url"],
                api_key=api_key,
                model_name=binding["model_name"],
                options=binding["options"],
                network_scope=str(provider.get("networkScope") or "public"),
            )
            probe_results.append(probe.to_dict())
        if not all(bool(item.get("ok")) for item in probe_results):
            return {
                "ok": False,
                "saved": False,
                "tests": probe_results,
                "summary": "专项模型测试未全部通过，配置未保存",
            }
        if embedding_storage is not None:
            dimension = next((item.get("embeddingDimension") for item in probe_results
                              if item.get("role") == "embedding"), None)
            if dimension != embedding_storage["dimension"]:
                raise ValueError(
                    f"嵌入模型输出 {dimension} 维，当前索引要求 {embedding_storage['dimension']} 维"
                )

        network_scope = str(provider.get("networkScope") or "public").strip().lower()
        if network_scope not in {"public", "private"}:
            raise ValueError("networkScope 只能是 public 或 private")
        billing = str(provider.get("billing") or "metered").strip().lower()
        if billing not in {"metered", "subscription", "local"}:
            raise ValueError("billing 只能是 metered、subscription 或 local")

        old_credential: Mapping[str, Any] | None = None
        if not provider_id:
            base_id = f"specialized-{_provider_slug(display_name, base_url).removeprefix('custom-')}"
            provider_id = base_id

        async for session in get_session():
            if not provider.get("providerId") and not provider.get("provider_id"):
                suffix = 2
                while (
                    await session.execute(
                        text("SELECT 1 FROM llm_providers WHERE id = :provider_id"),
                        {"provider_id": provider_id},
                    )
                ).first() is not None:
                    provider_id = f"{base_id}-{suffix}"
                    suffix += 1

            existing_provider = (
                await session.execute(
                    text("SELECT * FROM llm_providers WHERE id = :provider_id"),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if existing_provider is not None and str(existing_provider.get("driver")) not in {
                "specialized",
            }:
                raise ModelConfigConflict(
                    f"供应商 {provider_id} 已用于通用聊天，不能直接改为专项供应商"
                )

            if existing_provider is None:
                await session.execute(
                    text(
                        "INSERT INTO llm_providers "
                        "(id, display_name, driver, base_url, network_scope, "
                        "extra_headers, billing, is_builtin, enabled, created_by, updated_at) "
                        "VALUES (:id, :display_name, 'specialized', :base_url, :network_scope, "
                        "'{}'::jsonb, :billing, false, true, :operator, now())"
                    ),
                    {
                        "id": provider_id,
                        "display_name": display_name,
                        "base_url": base_url,
                        "network_scope": network_scope,
                        "billing": billing,
                        "operator": operator,
                    },
                )
            else:
                await session.execute(
                    text(
                        "UPDATE llm_providers SET display_name = :display_name, "
                        "base_url = :base_url, network_scope = :network_scope, "
                        "billing = :billing, enabled = true, updated_at = now() "
                        "WHERE id = :provider_id"
                    ),
                    {
                        "provider_id": provider_id,
                        "display_name": display_name,
                        "base_url": base_url,
                        "network_scope": network_scope,
                        "billing": billing,
                    },
                )

            old_credential = (
                await session.execute(
                    text(
                        "SELECT key_fingerprint, key_last4, key_version, updated_at, updated_by "
                        "FROM llm_provider_credentials WHERE provider_id = :provider_id"
                    ),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if api_key_input or old_credential is None:
                secret = encrypt_secret(api_key)
                new_fingerprint = fingerprint(api_key)
                new_last4 = last4(api_key)
                version = int((old_credential or {}).get("key_version") or 0) + 1
                await session.execute(
                    text(
                        "INSERT INTO llm_provider_credentials "
                        "(provider_id, key_cipher, key_fingerprint, key_last4, key_version, updated_by, updated_at) "
                        "VALUES (:provider_id, :key_cipher, :fingerprint, :last4, :version, :operator, now()) "
                        "ON CONFLICT (provider_id) DO UPDATE SET key_cipher = EXCLUDED.key_cipher, "
                        "key_fingerprint = EXCLUDED.key_fingerprint, key_last4 = EXCLUDED.key_last4, "
                        "key_version = EXCLUDED.key_version, updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "provider_id": provider_id,
                        "key_cipher": secret,
                        "fingerprint": new_fingerprint,
                        "last4": new_last4,
                        "version": version,
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, secret_fingerprint, operator, rollbackable) "
                        "VALUES ('provider_credential', :key, :fingerprint, :operator, false)"
                    ),
                    {
                        "key": provider_id,
                        "fingerprint": new_fingerprint,
                        "operator": operator,
                    },
                )

            for binding, probe in zip(bindings, probe_results, strict=True):
                # 专项模型也进入统一模型目录，供供应商页展示、角色下拉选择和
                # 后端用途校验；专项适配器的 base_url/options 仍保留在绑定表。
                await self._upsert_model(
                    session,
                    provider_id=provider_id,
                    model_name=binding["model_name"],
                    model_kind=binding["role"],
                    operator=operator,
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_model_role_bindings "
                        "(role, model_name, updated_by, updated_at) "
                        "VALUES (:role, :model_name, :operator, now()) "
                        "ON CONFLICT (role) DO UPDATE SET model_name = EXCLUDED.model_name, "
                        "updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "role": binding["role"],
                        "model_name": binding["model_name"],
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_specialized_model_bindings "
                        "(role, provider_id, adapter, model_name, base_url, options, enabled, "
                        "last_probe_at, last_probe_ok, last_probe_summary, last_probe_elapsed_ms, "
                        "updated_by, updated_at) "
                        "VALUES (:role, :provider_id, :adapter, :model_name, :base_url, "
                        "CAST(:options AS jsonb), true, now(), :probe_ok, :probe_summary, "
                        ":probe_elapsed_ms, :operator, now()) "
                        "ON CONFLICT (role) DO UPDATE SET provider_id = EXCLUDED.provider_id, "
                        "adapter = EXCLUDED.adapter, model_name = EXCLUDED.model_name, "
                        "base_url = EXCLUDED.base_url, options = EXCLUDED.options, enabled = true, "
                        "last_probe_at = now(), last_probe_ok = EXCLUDED.last_probe_ok, "
                        "last_probe_summary = EXCLUDED.last_probe_summary, "
                        "last_probe_elapsed_ms = EXCLUDED.last_probe_elapsed_ms, "
                        "updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "role": binding["role"],
                        "provider_id": provider_id,
                        "adapter": binding["adapter"],
                        "model_name": binding["model_name"],
                        "base_url": binding["base_url"],
                        "options": json.dumps(binding["options"], ensure_ascii=False),
                        "probe_ok": bool(probe["ok"]),
                        "probe_summary": probe["summary"],
                        "probe_elapsed_ms": probe["elapsedMs"],
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, old_value, new_value, operator) "
                        "VALUES ('role', :role, NULL, :new_value, :operator)"
                    ),
                    {
                        "role": binding["role"],
                        "new_value": binding["model_name"],
                        "operator": operator,
                    },
                )
            await session.commit()
            break

        await registry_store.refresh_registry()
        credential = {
            "configured": True,
            "fingerprint": fingerprint(api_key),
            "last4": last4(api_key),
            "rotatedAt": None,
            "rotatedBy": operator,
        }
        return {
            "ok": True,
            "saved": True,
            "provider": {
                "id": provider_id,
                "displayName": display_name,
                "driver": "specialized",
                "baseUrl": base_url,
                "credential": credential,
            },
            "tests": probe_results,
            "bindings": [
                {
                    "role": binding["role"],
                    "modelName": binding["model_name"],
                    "adapter": binding["adapter"],
                    "requiresReindex": binding["role"] == "embedding",
                }
                for binding in bindings
            ],
        }


    async def list_specialized(self) -> dict[str, Any]:
        """返回专项绑定与适配器清单，响应只包含脱敏凭据元数据。"""
        snap = await registry_store.load_registry()
        providers = {
            str(row.get("id")): row
            for row in snap.providers
            if row.get("id")
        }
        items: list[dict[str, Any]] = []
        for role in ("embedding", "rerank"):
            binding = snap.specialized.get(role)
            if not binding:
                continue
            provider_id = str(binding.get("provider_id") or "")
            provider = providers.get(provider_id) or {}
            meta = snap.credential_meta.get(provider_id) or {}
            items.append(
                {
                    "role": role,
                    "modelName": binding.get("model_name") or "",
                    "providerId": provider_id,
                    "providerName": provider.get("display_name") or provider_id,
                    "adapter": binding.get("adapter") or "",
                    "baseUrl": binding.get("base_url") or "",
                    "options": binding.get("options") or {},
                    "enabled": bool(binding.get("enabled", True)),
                    "credential": {
                        "configured": provider_id in snap.credentials and bool(meta),
                        "fingerprint": meta.get("fingerprint"),
                        "last4": meta.get("last4"),
                    },
                    "lastProbe": (
                        {
                            "ok": binding.get("last_probe_ok"),
                            "summary": binding.get("last_probe_summary"),
                            "elapsedMs": binding.get("last_probe_elapsed_ms"),
                            "at": _iso(binding.get("last_probe_at")),
                        }
                        if binding.get("last_probe_at") is not None
                        else None
                    ),
                    "requiresReindex": role == "embedding",
                }
            )
        return {
            "items": items,
            "adapters": [
                "dashscope_embedding",
                "openai_embedding",
                "dashscope_rerank",
                "jina_rerank",
            ],
            "providerBaseUrl": next(
                (
                    str(provider.get("base_url") or "").strip().rstrip("/")
                    for provider in providers.values()
                    if any(
                        item.get("providerId") == provider.get("id")
                        for item in items
                    )
                ),
                "",
            ),
            "source": "db" if snap.loaded else "fallback",
        }

