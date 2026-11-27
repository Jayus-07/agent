"""模型治理中心 - 角色模型映射、Fallback 解析与运行策略。"""
from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from sqlalchemy import text

from backend.config import model_roles as _real_model_roles
from backend.infra.llm import models as _real_models_mod, registry_store as _real_registry_store
from backend.memory.database import get_session as _real_get_session
from backend.services.model_governance.base import (
    DependencyProxy,
    ModelConfigConflict,
    ModelConfigNotFound,
    _iso,
    _json_value,
)
from backend.services.model_governance.models import _model_validation_issue as _real_model_validation_issue

get_session = DependencyProxy("get_session", _real_get_session)
model_roles = DependencyProxy("model_roles", _real_model_roles)
models_mod = DependencyProxy("models_mod", _real_models_mod)
registry_store = DependencyProxy("registry_store", _real_registry_store)
_model_validation_issue = DependencyProxy("_model_validation_issue", _real_model_validation_issue)


class RoleConfigMixin:
    """角色模型绑定与推理策略管理能力。"""

    async def update_role(self, role: str, model_name: str, operator: str, *,
                          confirm_rebuild: bool = False,
                          expected_model: str | None = None,
                          expected_fingerprint: str | None = None) -> dict:
        if role not in model_roles.MODEL_ROLES:
            raise ModelConfigNotFound(f"未注册的模型角色：{role}")
        model_name = (model_name or "").strip()
        issue = _model_validation_issue(model_name, role=role)
        if issue:
            raise ValueError(issue)

        preview = None
        if role == "embedding":
            # 后端独立检查，直接调用 API 也不能绕过维度/确认边界。
            preview = await self.preview_embedding_change(model_name)
            if expected_model is None or preview["currentModel"] != expected_model:
                raise ModelConfigConflict("嵌入模型配置已变化，请刷新后重新确认")
            if expected_fingerprint != preview["fingerprint"]:
                raise ModelConfigConflict("模型或供应商配置已变化，请重新检查")
            if preview["requiresRebuild"] and not confirm_rebuild:
                raise ModelConfigConflict("切换后检索将暂停，需确认全量重新生成向量")

        old_value: str | None = None
        async for session in get_session():
            if role == "embedding":
                from backend.models.task import TaskStatus
                from backend.services import task_service

                # 并发管理请求通过同一数据库事务锁串行，再校验旧绑定。
                await session.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtext('embedding-role-change'))"
                ))
                latest = task_service.get_latest_biz_task("embedding_rebuild", "global")
                if latest and latest.status in (
                    TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED,
                ):
                    raise ModelConfigConflict("向量重建任务尚未结束，暂不能再次切换模型")
            row = (
                await session.execute(
                    text(
                        "SELECT model_name FROM llm_model_role_bindings "
                        "WHERE role = :role"
                    ),
                    {"role": role},
                )
            ).first()
            if row is not None:
                old_value = str(row[0])
            if role == "embedding":
                effective_old = old_value or model_roles.resolve_effective("embedding")["value"]
                if effective_old != expected_model:
                    raise ModelConfigConflict("另一位管理员已修改嵌入模型，请重新确认")
                if preview["vectors"] == 0:
                    from backend.rag.indexing.embedding_migration import activate_empty_space

                    activate_empty_space(model_name, preview["fingerprint"],
                                         preview["provider"], preview["dimension"])
                elif preview["requiresRebuild"]:
                    from backend.rag.indexing.embedding_migration import mark_required

                    # 先关闭旧索引，配置写入失败最多造成明确暂停，不会混用向量。
                    mark_required()
            await session.execute(
                text(
                    "INSERT INTO llm_model_role_bindings "
                    "(role, model_name, updated_by, updated_at) "
                    "VALUES (:role, :model_name, :operator, now()) "
                    "ON CONFLICT (role) DO UPDATE SET "
                    "model_name = EXCLUDED.model_name, "
                    "updated_by = EXCLUDED.updated_by, "
                    "updated_at = now()"
                ),
                {"role": role, "model_name": model_name, "operator": operator},
            )
            if old_value != model_name:
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, old_value, new_value, operator) "
                        "VALUES ('role', :role, :old_value, :new_value, :operator)"
                    ),
                    {
                        "role": role,
                        "old_value": old_value,
                        "new_value": model_name,
                        "operator": operator,
                    },
                )
                # embedding 换模型 = 换语义空间：既有索引立即进入待重建状态
                # （治理改造 2026-09-22）。查询门禁（pgvector_store）会据此
                # 拒绝检索并返回 INDEX_EMBEDDING_MISMATCH，重建写回后恢复 ready。
            # get_session() 是 async generator；break 会提前结束迭代，无法执行
            # 生成器 yield 后的自动 commit。写入必须在 break 前显式提交。
            await session.commit()
            break

        model_roles.set_override(
            role,
            model_name,
            {"updatedBy": operator, "updatedAt": None},
        )
        await registry_store.refresh_registry()
        return {
            "role": role,
            "oldValue": old_value,
            "newValue": model_name,
            "changedBy": operator,
            "requiresReindex": model_roles.MODEL_ROLES[role].requires_reindex,
        }


    @staticmethod
    def _validate_policy_payload(role: str, payload: Mapping[str, Any]) -> dict:
        """校验策略字段；返回归一化后的写入值。"""
        defaults = model_roles.runtime_defaults(role)
        fallback_model = str(payload.get("fallbackModel") or "").strip()
        timeout_seconds = payload.get(
            "timeoutSeconds", defaults.timeout_seconds
        )
        max_retries = payload.get("maxRetries", defaults.max_retries)
        failure_policy = str(
            payload.get("failurePolicy") or defaults.failure_policy
        )
        try:
            timeout_seconds = int(timeout_seconds)
            max_retries = int(max_retries)
        except (TypeError, ValueError):
            raise ValueError("timeoutSeconds / maxRetries 必须是整数")
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("timeoutSeconds 必须在 1~600 之间")
        if not 0 <= max_retries <= 3:
            raise ValueError("maxRetries 必须在 0~3 之间（预算封顶，禁止 60s×N）")
        if failure_policy not in model_roles.FAILURE_POLICIES:
            raise ValueError(
                f"failurePolicy 必须是 {model_roles.FAILURE_POLICIES} 之一"
            )
        if role == "multi_query" and failure_policy not in {"skip", "fallback"}:
            raise ValueError(
                "multi_query 角色仅支持 skip 或 fallback 策略"
            )
        # embedding 禁止 fallback：静默换语义空间会让新向量查旧索引
        if role == "embedding" and failure_policy == "fallback":
            raise ValueError(
                "embedding 角色禁止 fallback 策略：不同 embedding 模型不共享"
                "语义空间，必须 fail_fast 后重建索引"
            )
        if fallback_model:
            issue = _model_validation_issue(fallback_model, role=role)
            if issue:
                raise ValueError(f"fallback 模型非法：{issue}")
            if fallback_model == str(
                model_roles.resolve_effective(role).get("value") or ""
            ):
                raise ValueError("fallback 模型不能与 primary 相同")
        return {
            "fallback_model": fallback_model,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "failure_policy": failure_policy,
        }


    async def get_role_policy(self, role: str) -> dict:
        """角色的运行策略：DB 覆盖优先，否则代码默认（含来源标注）。"""
        if role not in model_roles.MODEL_ROLES:
            raise ModelConfigNotFound(f"未注册的模型角色：{role}")
        defaults = model_roles.runtime_defaults(role)
        row: Mapping[str, Any] | None = None
        async for session in get_session():
            row = (
                await session.execute(
                    text(
                        "SELECT fallback_model, timeout_seconds, max_retries, "
                        "failure_policy, updated_by, updated_at "
                        "FROM llm_model_role_policy WHERE role = :role"
                    ),
                    {"role": role},
                )
            ).mappings().first()
            break
        if row is None:
            return {
                "role": role,
                "fallbackModel": defaults.fallback_model,
                "timeoutSeconds": defaults.timeout_seconds,
                "maxRetries": defaults.max_retries,
                "failurePolicy": defaults.failure_policy,
                "source": "default",
                "updatedBy": None,
                "updatedAt": None,
            }
        return {
            "role": role,
            "fallbackModel": str(row["fallback_model"] or ""),
            "timeoutSeconds": int(row["timeout_seconds"]),
            "maxRetries": int(row["max_retries"]),
            "failurePolicy": str(row["failure_policy"]),
            "source": "db",
            "updatedBy": row["updated_by"],
            "updatedAt": _iso(row["updated_at"]),
        }


    async def update_role_policy(
        self, role: str, payload: Mapping[str, Any], operator: str,
    ) -> dict:
        """写入角色运行策略（含审计历史）。"""
        if role not in model_roles.MODEL_ROLES:
            raise ModelConfigNotFound(f"未注册的模型角色：{role}")
        normalized = self._validate_policy_payload(role, payload)
        old_policy = await self.get_role_policy(role)
        async for session in get_session():
            await session.execute(
                text(
                    "INSERT INTO llm_model_role_policy "
                    "(role, fallback_model, timeout_seconds, max_retries, "
                    " failure_policy, updated_by, updated_at) "
                    "VALUES (:role, :fallback_model, :timeout_seconds, "
                    "        :max_retries, :failure_policy, :operator, now()) "
                    "ON CONFLICT (role) DO UPDATE SET "
                    "fallback_model = EXCLUDED.fallback_model, "
                    "timeout_seconds = EXCLUDED.timeout_seconds, "
                    "max_retries = EXCLUDED.max_retries, "
                    "failure_policy = EXCLUDED.failure_policy, "
                    "updated_by = EXCLUDED.updated_by, "
                    "updated_at = now()"
                ),
                {"role": role, "operator": operator, **normalized},
            )
            await session.execute(
                text(
                    "INSERT INTO llm_config_history "
                    "(object_type, object_key, old_value, new_value, operator) "
                    "VALUES ('role_policy', :role, :old_value, :new_value, :operator)"
                ),
                {
                    "role": role,
                    "old_value": _json_value(old_policy),
                    "new_value": _json_value(normalized),
                    "operator": operator,
                },
            )
            await session.commit()
            break
        return {
            "role": role,
            **{k: normalized[k] for k in (
                "fallback_model", "timeout_seconds", "max_retries",
                "failure_policy")},
            "changedBy": operator,
        }

