"""sys_model_roles.py — 模型角色绑定视图（P2，tab①⑤ 的数据源）

| 端点 | 用途 |
|---|---|
| `GET /sys/model-roles` | 全部模型角色的生效值、来源与可用性（只读） |

字段对齐 UI 设计 §5.4 `RoleBinding`（**该文档即契约**）。端点层做三件事：

1. **`source` 归一化**：`model_roles` 内部返回 `'code-default'`（契约是 `'default'`），
   继承态更返回 `'inherit:<父role>'`（契约只有 `'inherit'`，父 role 另在
   `inheritedFrom`）。在边界收敛，不改 P0 常量（它另有消费方与测试）。
2. **补三个派生字段** `provider` / `registered` / `missingKeyEnv`：`effective_snapshot()`
   只给「值 + 来源」，这三个要跨到 `infra/llm`；而 `model_roles` 是**纯 stdlib** 模块
   （硬约束：不得 import `infra.llm.*`，见主设计附录 A），故只能在端点层拼。
3. **`provider` 与 `registered` 同源**：都取**当前可用清单**（代码层 + DB 动态层）。
   不用 `model_roles.provider_of` —— 它只看代码层 `AVAILABLE_MODELS`，会让自建模型
   出现「registered=true 但 provider=null」的自相矛盾。

DB 角色覆盖由 `registry_store` 刷新时注入 `model_roles`，因此 `source=db` 是真实
可观测状态；无覆盖时仍按 `env` / `inherit` / `default` 返回。该路由已注册到
`api_router`，并与角色写入端点共享同一套模型清单。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.api.deps import require_user_actor
from backend.config import model_roles
from backend.config import llm as config_llm
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod

router = APIRouter(prefix="/sys/model-roles", tags=["sys-model-roles"])

# 内部常量 → §5.4 契约取值。只此一处映射，避免常量与文档两套口径漂移。
_SOURCE_ALIASES: dict[str, str] = {model_roles.SOURCE_DEFAULT: "default"}

_INHERIT_PREFIX = f"{model_roles.SOURCE_INHERIT}:"


def _schema_source(source: str) -> str:
    """内部 source → §5.4 `ValueSource`。

    - `code-default`            → `default`
    - `inherit:<父role>`        → `inherit`（父 role 走 `inheritedFrom` 字段）
    - `db` / `env`              → 原样
    """
    if source.startswith(_INHERIT_PREFIX):
        return model_roles.SOURCE_INHERIT
    return _SOURCE_ALIASES.get(source, source)


def _missing_key_env(provider: str | None) -> str | None:
    """兼容字段：数据库配置模式下不再返回 Key 环境变量名。

    Key 已迁移到加密数据库，前端不应再引导用户编辑 env；可操作原因由
    ``availabilityReason`` 返回。
    """
    del provider
    return None


def _availability_reason(
    *,
    role: str,
    effective: str,
    provider: str | None,
    registered: bool,
    missing_key_env: str | None,
    model_kind: str | None,
) -> str | None:
    """给管理端返回可直接展示的角色可用性原因。"""
    if not effective:
        return "未配置"
    if not registered:
        return "未注册"
    expected_kind = models_mod.expected_model_kind(role)
    if model_kind != expected_kind:
        return (
            f"用途不匹配：需要{models_mod.MODEL_KIND_LABELS[expected_kind]}，"
            f"当前是{models_mod.MODEL_KIND_LABELS.get(model_kind or 'chat', '未知用途')}"
        )
    if provider == "ollama" and not config_llm.OLLAMA_ENABLED:
        return "Ollama 当前未启用（cloud 模式禁用本地模型）"
    if missing_key_env:
        return f"缺少 {missing_key_env}"
    try:
        reason = credentials_mod.check_provider_usable(provider or "")
        if reason:
            return reason
        if provider != "ollama" and not credentials_mod.resolve_credentials(provider).api_key:
            return "未配置 API Key"
        return None
    except Exception:
        return "供应商可用性暂时无法确认"


@router.get("")
async def list_model_roles(ident=Depends(require_user_actor)) -> dict:
    """全部模型角色：生效模型、来源、归属 provider 与可用性（tab①⑤ 数据源）。

    响应裸 dict（§1.1.1 决策），字段见 §5.4 `RoleBinding`；`label` 不在其中
    （前端常量，契约明确不从后端取）。**不含任何密钥载体**。
    """
    # name → provider，一次遍历同时供 provider / registered 使用（同源，见模块头第 3 条）
    catalog = {
        str(m.get("name") or ""): m
        for m in models_mod.get_available_models()
        if m.get("name")
    }
    items: list[dict] = []
    for row in model_roles.effective_snapshot():
        effective = row["value"] or ""
        entry = catalog.get(effective) or {}
        provider = str(entry.get("provider") or "") or None
        registered = bool(effective) and effective in catalog
        missing_key_env = _missing_key_env(provider)
        availability_reason = _availability_reason(
            role=row["role"],
            effective=effective,
            provider=provider,
            registered=registered,
            missing_key_env=missing_key_env,
            model_kind=models_mod.model_kind_of(entry) if entry else None,
        )
        items.append({
            "role": row["role"],
            "effectiveModel": effective,
            "literalValue": row["rawValue"],
            "source": _schema_source(row["source"]),
            "inheritedFrom": row["inheritedFrom"],
            "provider": provider,
            "registered": registered,
            "missingKeyEnv": missing_key_env,
            "available": availability_reason is None and bool(effective),
            "availabilityReason": availability_reason,
            "requiresReindex": row["requiresReindex"],
            "updatedBy": row["updatedBy"],
            "updatedAt": row["updatedAt"],
        })
    return {"items": items, "actor": ident.actor}
