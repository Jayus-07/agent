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

⚠️ **读 `source` 前必读（DB 覆盖层现状）**：`model_roles.inject_overrides()` 至今
**零调用点** —— `services/sys_config.py::_fetch_overrides` 只捞 `_SWITCHES` 的键，
模型角色不登记在那张表里。故当前 `source` 只可能是 `env` / `inherit` / `default`；
`db` 是接线后的取值，本端点已能如实透传（有测试锁定）。详见
docs/model-config-admin-ui-design.md §15.4。

⚠️ **尚未注册**到 `api_router`（同 `sys_providers.py` 的处境）：`app/api/router.py`
被并发会话持有未提交改动，提交它会连带让主干 import 失败。待其落定后一并注册。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.api.deps import require_admin_user
from backend.config import model_roles
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
    """角色生效模型所属 provider 缺哪个 Key 环境变量；齐备 / 无需 Key → None。

    与 `credentials.missing_key_message` 的区别：后者给**可操作报错全文**（带
    `.env` 指引），契约这里只要 env 名 —— 文案由前端拼，避免两处措辞漂移。

    `ollama` 与「未登记 Key 的 provider」都返回 None：它们不属于「缺 Key」，
    可用性问题由 `check_provider_usable` / 探测按钮负责（kind 不同，勿混）。
    """
    if not provider or provider == "ollama":
        return None
    env_name = models_mod.PROVIDER_API_KEY_ENV.get(provider)
    if not env_name:
        return None
    return None if credentials_mod.resolve_credentials(provider).api_key else env_name


@router.get("")
async def list_model_roles(ident=Depends(require_admin_user)) -> dict:
    """全部模型角色：生效模型、来源、归属 provider 与可用性（tab①⑤ 数据源）。

    响应裸 dict（§1.1.1 决策），字段见 §5.4 `RoleBinding`；`label` 不在其中
    （前端常量，契约明确不从后端取）。**不含任何密钥载体**。
    """
    # name → provider，一次遍历同时供 provider / registered 使用（同源，见模块头第 3 条）
    catalog = {
        str(m.get("name") or ""): str(m.get("provider") or "")
        for m in models_mod.get_available_models()
        if m.get("name")
    }
    items: list[dict] = []
    for row in model_roles.effective_snapshot():
        effective = row["value"] or ""
        provider = catalog.get(effective) or None
        items.append({
            "role": row["role"],
            "effectiveModel": effective,
            "literalValue": row["rawValue"],
            "source": _schema_source(row["source"]),
            "inheritedFrom": row["inheritedFrom"],
            "provider": provider,
            "registered": bool(effective) and effective in catalog,
            "missingKeyEnv": _missing_key_env(provider),
            "requiresReindex": row["requiresReindex"],
            "updatedBy": row["updatedBy"],
            "updatedAt": row["updatedAt"],
        })
    return {"items": items, "actor": ident.actor}
