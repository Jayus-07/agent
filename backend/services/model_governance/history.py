"""模型治理中心 - 变更历史审计、快照回滚与配置漂移比对。"""
from __future__ import annotations

import json
from typing import Any
from collections.abc import Mapping
from sqlalchemy import text

from backend.config import model_roles as _real_model_roles
from backend.infra.llm import (
    credentials as _real_credentials_mod,
    registry_store as _real_registry_store,
)
from backend.memory.database import get_session as _real_get_session
from backend.services.model_governance.base import (
    DependencyProxy,
    ModelConfigConflict,
    ModelConfigNotFound,
    _iso,
    _json_value,
)

get_session = DependencyProxy("get_session", _real_get_session)
registry_store = DependencyProxy("registry_store", _real_registry_store)
model_roles = DependencyProxy("model_roles", _real_model_roles)
credentials_mod = DependencyProxy("credentials_mod", _real_credentials_mod)


class HistoryMixin:
    """历史变更追踪、审计回滚与配置漂移分析能力。"""

    async def list_history(
        self, object_type: str | None = None, limit: int = 200
    ) -> dict:
        if object_type and object_type not in HISTORY_OBJECTS:
            raise ValueError(f"object 只能是 {sorted(HISTORY_OBJECTS)}")
        params: dict[str, Any] = {"limit": limit}
        where = ""
        if object_type:
            where = "WHERE object_type = :object_type"
            params["object_type"] = object_type
        items: list[dict] = []
        async for session in get_session():
            rows = (
                await session.execute(
                    text(
                        "SELECT h.*, u.username AS operator_username "
                        "FROM (SELECT * FROM llm_config_history "
                        f"{where} ORDER BY changed_at DESC, id DESC LIMIT :limit) h "
                        "LEFT JOIN auth.users u ON h.operator = 'user:' || u.id::text "
                        "ORDER BY h.changed_at DESC, h.id DESC"
                    ),
                    params,
                )
            ).mappings().all()
            for row in rows:
                items.append(
                    {
                        "id": str(row["id"]),
                        "object": row["object_type"],
                        "key": row["object_key"],
                        "oldValue": row["old_value"],
                        "newValue": row["new_value"],
                        "secretFingerprint": row["secret_fingerprint"],
                        "operator": row["operator"],
                        "operatorName": (
                            row.get("operator_username")
                            or (
                                f"未知用户（ID：{row['operator'][5:]}）"
                                if str(row["operator"] or "").startswith("user:")
                                else "系统" if row["operator"] in ("system", "")
                                else f"自动化：{row['operator']}"
                            )
                        ),
                        "at": _iso(row["changed_at"]),
                        "rollbackable": bool(row["rollbackable"]),
                    }
                )
            break
        return {"items": items}


    async def rollback(self, history_id: int, operator: str, *,
                       allow_local_billing: bool = False) -> dict:
        result: dict[str, Any] = {}
        role_refresh: tuple[str, str] | None = None
        async for session in get_session():
            row = (
                await session.execute(
                    text("SELECT * FROM llm_config_history WHERE id = :history_id"),
                    {"history_id": history_id},
                )
            ).mappings().first()
            if row is None:
                raise ModelConfigNotFound(f"未找到历史记录：{history_id}")
            if row["object_type"] == "provider_credential" or not row["rollbackable"]:
                raise ModelConfigConflict("密钥历史不可回滚，请重新轮换密钥")

            object_type = row["object_type"]
            key = row["object_key"]
            target = row["old_value"]
            from backend.infra.llm import specialized

            binding = specialized.resolve_binding("embedding")
            if ((object_type == "role" and key == "embedding")
                    or (binding and object_type in {"provider", "provider_network_scope"}
                        and key == binding.provider_id)):
                raise ModelConfigConflict(
                    "嵌入配置不能绕过兼容性检查直接回滚，请在角色绑定中选择历史模型并确认重建"
                )
            if object_type == "role":
                if target is None:
                    await session.execute(
                        text("DELETE FROM llm_model_role_bindings WHERE role = :role"),
                        {"role": key},
                    )
                else:
                    await session.execute(
                        text(
                            "INSERT INTO llm_model_role_bindings "
                            "(role, model_name, updated_by, updated_at) VALUES "
                            "(:role, :model_name, :operator, now()) "
                            "ON CONFLICT (role) DO UPDATE SET model_name = EXCLUDED.model_name, "
                            "updated_by = EXCLUDED.updated_by, updated_at = now()"
                        ),
                        {"role": key, "model_name": target, "operator": operator},
                    )
                    role_refresh = (key, target)
            elif object_type == "provider_network_scope":
                if target not in {"public", "private"}:
                    raise ModelConfigConflict("历史中的 network_scope 非法，拒绝回滚")
                await session.execute(
                    text(
                        "UPDATE llm_providers SET network_scope = :scope, updated_at = now() "
                        "WHERE id = :provider_id"
                    ),
                    {"scope": target, "provider_id": key},
                )
            elif object_type == "provider":
                try:
                    previous = json.loads(target or "{}")
                except json.JSONDecodeError as exc:
                    raise ModelConfigConflict("供应商历史格式损坏，拒绝回滚") from exc
                if previous.get("billing") == "local" and not allow_local_billing:
                    raise ModelConfigConflict("恢复本地计费声明需要超级管理员权限")
                await session.execute(
                    text(
                        "UPDATE llm_providers SET display_name = :display_name, "
                        "driver = :driver, base_url = :base_url, billing = :billing, "
                        "enabled = :enabled, updated_at = now() WHERE id = :provider_id"
                    ),
                    {
                        "provider_id": key,
                        "display_name": previous.get("displayName") or key,
                        "driver": previous.get("driver") or "openai",
                        "base_url": previous.get("baseUrl") or "",
                        "billing": previous.get("billing") or "metered",
                        "enabled": bool(previous.get("enabled", True)),
                    },
                )
            else:
                raise ModelConfigConflict(f"不支持回滚的历史类型：{object_type}")

            await session.execute(
                text(
                    "INSERT INTO llm_config_history "
                    "(object_type, object_key, old_value, new_value, operator, rollbackable) "
                    "VALUES (:object_type, :key, :old_value, :new_value, :operator, true)"
                ),
                {
                    "object_type": object_type,
                    "key": key,
                    "old_value": row["new_value"],
                    "new_value": target,
                    "operator": operator,
                },
            )
            result = {
                "id": str(history_id),
                "rolledBack": True,
                "changedBy": operator,
                "object": object_type,
                "key": key,
            }
            await session.commit()
            break

        if role_refresh:
            model_roles.set_override(
                role_refresh[0], role_refresh[1], {"updatedBy": operator, "updatedAt": None}
            )
        await registry_store.refresh_registry()
        return result


    async def drift(self) -> dict:
        snap = await registry_store.load_registry()
        if snap.loaded:
            models_mod.set_dynamic_providers(snap.providers)
            models_mod.set_dynamic_models(snap.models)
            credentials_mod.set_db_credentials(snap.credentials)
            model_roles.inject_overrides(snap.roles, snap.role_meta)

        catalog = {
            str(item.get("name")): str(item.get("provider") or "")
            for item in models_mod.get_available_models()
            if item.get("name")
        }
        items: list[dict] = []
        for role, spec in model_roles.MODEL_ROLES.items():
            effective = model_roles.resolve_effective(role)
            name = str(effective.get("value") or "")
            if spec.validator and name and name not in catalog:
                items.append(
                    {
                        "severity": "critical",
                        "kind": "unregistered_model",
                        "subject": role,
                        "message": f"角色 {role} 的模型 {name} 未注册",
                        "hint": "先在模型注册表登记该模型，或改回已注册模型。",
                    }
                )
            provider = catalog.get(name)
            missing_key_reason = (
                credentials_mod.missing_key_message(provider)
                if provider
                else None
            )
            if missing_key_reason:
                items.append(
                    {
                        "severity": "critical",
                        "kind": "missing_key",
                        "subject": role,
                        "message": f"角色 {role} 不可用：{missing_key_reason}",
                        "hint": "在供应商页配置并测试数据库凭据。",
                    }
                )

        if snap.loaded:
            # P1（2026-10-24）状态口径统一：供应商级探测（provider 表）与
            # 模型级健康扫描（llm_model_health）此前是两座孤岛——正在被
            # 角色使用的供应商上游宕机，只会躺在绑定行的手动测试里，汇总
            # 与体检全绿。这里把模型健康并入漂移判定：
            #   任一模型最近探测失败 → critical（含本地服务的针对性指引）
            #   两级都无任何探测数据 → warn（真·未验证）
            #   仅供应商级探测失败 → warn（维持原语义）
            try:
                health_rows = await self.list_model_health()
            except Exception:  # noqa: BLE001 — 健康缓存读失败不阻塞其余体检项
                logger.warning("[ModelConfig] drift 读取模型健康缓存失败")
                health_rows = []
            health_by_provider: dict[str, list[dict]] = {}
            for hr in health_rows:
                health_by_provider.setdefault(str(hr.get("provider") or ""), []).append(hr)
            for provider in snap.providers:
                pid = str(provider.get("id") or "")
                probed_at = provider.get("last_probe_at")
                hrows = health_by_provider.get(pid, [])
                down = [
                    h for h in hrows
                    if h.get("status") and h.get("status") not in ("healthy", "slow")
                ]
                if down:
                    worst = max(
                        down, key=lambda h: int(h.get("consecutiveFailures") or 0),
                    )
                    names = "、".join(str(h.get("modelName")) for h in down[:3])
                    billing = str(hrows[0].get("billing") or "metered")
                    hint = (
                        "本地推理服务可能未随本次部署启动：请在服务器上启动对应"
                        "服务进程，或将相关角色改绑云端模型；服务恢复后下次探测"
                        "会自动转绿。"
                        if billing == "local"
                        else "查看角色行「手动测试」的分级结果并修正 URL、密钥或"
                        "模型名；或改绑其他可用模型。"
                    )
                    items.append(
                        {
                            "severity": "critical",
                            "kind": "provider_model_unreachable",
                            "subject": pid,
                            "message": (
                                f"供应商 {pid} 有 {len(down)} 个模型健康探测失败"
                                f"（{names}；连续失败 {worst.get('consecutiveFailures')} 次）"
                            ),
                            "hint": hint,
                        }
                    )
                elif probed_at is None and not hrows:
                    items.append(
                        {
                            "severity": "warn",
                            "kind": "unverified_provider",
                            "subject": pid,
                            "message": f"供应商 {pid} 尚未完成连通性探测",
                            "hint": "在供应商页点击测试，确认 URL、密钥与模型均可用。",
                        }
                    )
                elif (
                    probed_at is not None
                    and not provider.get("last_probe_ok")
                    and not hrows
                ):
                    items.append(
                        {
                            "severity": "warn",
                            "kind": "unverified_provider",
                            "subject": pid,
                            "message": f"供应商 {pid} 最近一次探测未通过",
                            "hint": "查看探测分级结果并修正对应的 URL、密钥或模型名。",
                        }
                    )
        else:
            items.append(
                {
                    "severity": "critical",
                    "kind": "unverified_provider",
                    "subject": "registry",
                    "message": "模型注册表数据库不可用，当前仅使用代码层兜底",
                    "hint": "先执行记忆库迁移并检查数据库连接。",
                }
            )

        # 当前仓库没有统一的索引元数据表；明确报告“不可判定”，不伪造全绿。
        items.append(
            {
                "severity": "info",
                "kind": "index_model_mismatch",
                "subject": "embedding",
                "message": "索引元数据尚未接入，暂时无法自动比对向量模型",
                "hint": "切换 embedding 模型后必须人工确认并全量重建索引。",
            }
        )
        return {
            "items": items,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }

