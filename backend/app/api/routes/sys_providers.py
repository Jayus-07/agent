"""sys_providers.py — LLM 供应商清单与连通性探测端点（P1b + P2 数据源）

| 端点 | 用途 | 限流 |
|---|---|---|
| `GET  /sys/providers` | 供应商清单（tab② 列表数据源，只读） | — |
| `GET  /sys/providers/presets` | 预置端点目录（新增/编辑抽屉的厂商·协议候选，只读静态数据） | — |
| `POST /sys/providers/{provider_id}/verify` | 已存实例复测（默认快速，`mode=full` 才检查流式 usage） | admin · 10 次/分 |
| `POST /sys/providers/verify-draft` | 草稿态探测（body 带 driver/base_url/apiKey/scope） | admin · **5 次/分**（更严） |

后两个端点落实 B.6 的**四道限制**（否则它就是一个「任意 URL 探测代理」）：

1. **admin only** —— 复用 `require_admin_user`（kind=user + role=admin）。
2. **目标过 `url_guard`** —— 私网只在实例 `network_scope='private'` 时放行，
   且该值来自 DB 显式勾选，不由「解析结果」推导（防 DNS rebinding）。
3. **报文固定** —— body 只有 driver/base_url/api_key/model_name/network_scope，
   不接受自定义 prompt / body / 任意 header；探测用固定 prompt 与 `max_tokens=16`。
   默认快速模式只跑到 L2，`mode=full` 才检查流式 usage。
4. **限流** —— 进程内滑动窗口，按 actor 分桶；草稿态配额更小。

响应一律**裸 dict**（§1.1.1 决策，无 Result 壳）。审计走 logger：
**who / 目标 URL / network_scope / 分级结论 / 耗时，不记密钥**（B.6）。

本路由已注册到 `api_router`，并与模型配置写入、历史和漂移端点一起作为管理端
模型配置域的后端入口。`lastProbe` 由 provider 表中的最近探测字段提供，服务
重启后仍可展示最近一次结果。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.deps import require_admin_user
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm import provider_presets
from backend.infra.llm import registry_store
from backend.services import provider_probe
from backend.services.model_config import get_model_config_service
from backend.shared.logger import logger

router = APIRouter(prefix="/sys/providers", tags=["sys-providers"])

_VERIFY_PER_MIN = 10
_DRAFT_VERIFY_PER_MIN = 5
_WINDOW_S = 60.0

_hits: dict[str, deque[float]] = {}


def _enforce_rate_limit(actor: str, bucket: str, limit: int) -> None:
    """按 actor 分桶的滑动窗口限流（进程内）。"""
    now = time.monotonic()
    dq = _hits.setdefault(f"{bucket}:{actor}", deque())
    while dq and now - dq[0] > _WINDOW_S:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"探测过于频繁（每分钟上限 {limit} 次），请稍后再试",
        )
    dq.append(now)


def reset_rate_limit_for_tests() -> None:
    """测试态：清空限流窗口。"""
    _hits.clear()


class DraftProbeRequest(BaseModel):
    """草稿态探测请求（未落库的供应商配置）。字段严格受限 —— 见模块头第 3 条。"""

    model_config = ConfigDict(populate_by_name=True)

    driver: str = Field(..., min_length=1, description="openai / anthropic / ollama")
    base_url: str = Field(
        ..., alias="baseUrl", min_length=1, description="供应商端点根地址"
    )
    model_name: str = Field(
        ..., alias="modelName", min_length=1, description="用于验证的模型名"
    )
    model_kind: Literal["chat", "embedding", "rerank", "vision", "speech"] = Field(
        "chat", alias="modelKind", description="模型用途"
    )
    api_key: str = Field("", alias="apiKey", description="草稿密钥；ollama 可留空")
    network_scope: str = Field(
        "public", alias="networkScope", description="public | private"
    )


def _normalize_scope(value: str | None) -> str:
    return "private" if (value or "").strip().lower() == "private" else "public"


def _find_provider(snap: registry_store.RegistrySnapshot, provider_id: str) -> dict | None:
    return next((p for p in snap.providers if str(p.get("id")) == provider_id), None)


def _credential_for(snap: registry_store.RegistrySnapshot, provider_id: str):
    """取 DB 凭据并补齐未被 DB 覆盖的 env 字段。

    注意：`resolve_credentials` 对不在代码层注册表里的 provider 会抛
    `UnknownProviderError`（自建 provider 属此列），故必须先查快照。
    """
    snapshot_credential = None
    for key, cred in snap.credentials.items():
        if str(key) == provider_id:
            snapshot_credential = cred
            break

    # DB 可能只覆盖 provider 表的 Base URL，快照中的凭据此时没有 api_key。
    # 仍需从统一入口取 env Key，再保留快照的地址和请求头覆盖；否则复测会
    # 把“只改地址”误判为“没有凭据”。
    if snapshot_credential is not None and snapshot_credential.api_key:
        return snapshot_credential
    try:
        resolved = credentials_mod.resolve_credentials(provider_id)
    except credentials_mod.UnknownProviderError:
        return snapshot_credential
    if snapshot_credential is None:
        return resolved
    if resolved is None:
        return snapshot_credential
    return replace(
        snapshot_credential,
        api_key=snapshot_credential.api_key or resolved.api_key,
        base_url=snapshot_credential.base_url or resolved.base_url,
        extra_headers={
            **dict(resolved.extra_headers or {}),
            **dict(snapshot_credential.extra_headers or {}),
        },
        extra_body={
            **dict(resolved.extra_body or {}),
            **dict(snapshot_credential.extra_body or {}),
        },
    )


# ── GET /sys/providers：供应商清单（只读）─────────────────────────────────
#
# 字段对齐 UI 设计 §6 `ProviderRow`。两条数据分支：
#   DB 可用   → 快照（含管理员自建实例）
#   DB 不可用 → **fail-open 兜底为代码层内置厂商 + env 凭据状态**
# 兜底分支绝不能返回空列表：那会让管理端在库未就绪时显示「一个供应商都没有」，
# 把运维引向「谁把配置删了」的错误方向。


def _count_models_by_provider(entries) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in entries:
        pid = str(m.get("provider") or "")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _models_by_provider(entries) -> dict[str, list[dict]]:
    """按供应商返回模型目录，并补齐历史条目的用途类型。"""
    grouped: dict[str, list[dict]] = {}
    for item in entries:
        provider = str(item.get("provider") or "")
        name = str(item.get("name") or "")
        if not provider or not name:
            continue
        grouped.setdefault(provider, []).append({
            "name": name,
            "display": item.get("display") or name,
            "modelKind": models_mod.normalize_model_kind(item.get("model_kind")),
        })
    for values in grouped.values():
        values.sort(key=lambda value: (value["modelKind"], value["name"]))
    return grouped


def _decorate_models(
    grouped: dict[str, list[dict]],
    db_keys: set[tuple[str, str]],
    roles_by_model: dict[str, list[str]],
) -> None:
    """就地为模型条目补 `source` 与 `usedByRoles`（管理端「移除模型」的判定字段）。

    - `source`：`user` = 在 `llm_models` 里有行（可移除）；`builtin` = 仅存在于代码层
      `AVAILABLE_MODELS`（移除无意义 —— 下次合并会原样回来，故不允许）。
    - `usedByRoles`：该模型正被哪些角色占用。**放在列表里而不是等到 409 才知道**，
      是为了让「移除」按钮能就地禁用并说明原因，而不是让用户点完再被拒。

    判定「是否在 DB」用 `(provider, name)` 是否出现在 `snap.models`，不依赖模型自带的
    `source` 字段（`_SELECT_MODELS` 并未选取该列，硬读会得到一个恒为默认值的假信号）。
    """
    for pid, items in grouped.items():
        for item in items:
            name = str(item.get("name") or "")
            item["source"] = "user" if (pid, name) in db_keys else "builtin"
            item["usedByRoles"] = sorted(roles_by_model.get(name, []))


def _roles_by_model(snap: registry_store.RegistrySnapshot) -> dict[str, list[str]]:
    """role → 模型名 反转为 模型名 → [role]（快照已带 roles，无需额外查询）。"""
    inverted: dict[str, list[str]] = {}
    for role, model_name in (snap.roles or {}).items():
        inverted.setdefault(str(model_name), []).append(str(role))
    return inverted


def _merged_models(snap: registry_store.RegistrySnapshot) -> list[dict]:
    """代码层模型 + DB 自建模型合并，避免 seeded provider 显示 0 个模型。"""
    merged = {
        (str(item.get("provider") or ""), str(item.get("name") or "")): item
        for item in models_mod.AVAILABLE_MODELS
        if item.get("provider") and item.get("name")
    }
    for item in snap.models:
        key = (str(item.get("provider") or ""), str(item.get("name") or ""))
        if all(key):
            merged[key] = item
    return list(merged.values())


def _credential_view(configured: bool, meta: dict | None) -> dict:
    """密钥状态视图，**只有布尔与脱敏指纹**。

    §7.3 硬约束 1：前端从不持有明文，连密文都不下发。
    """
    meta = meta or {}
    return {
        "configured": configured,
        "fingerprint": meta.get("fingerprint"),
        "last4": meta.get("last4"),
        "rotatedAt": meta.get("rotatedAt"),
        "rotatedBy": meta.get("rotatedBy"),
    }


def _builtin_rows() -> list[dict]:
    """DB 不可用时的兜底清单（代码层内置厂商 + env 凭据状态）。"""
    env = credentials_mod.snapshot()
    counts = _count_models_by_provider(models_mod.AVAILABLE_MODELS)
    grouped_models = _models_by_provider(models_mod.AVAILABLE_MODELS)
    # env 兜底分支没有 DB 行：全部是代码层内置模型 → 一律不可移除、无角色占用。
    _decorate_models(grouped_models, set(), {})
    rows: list[dict] = []
    for pid, meta in models_mod.PROVIDERS.items():
        cred = env.get(pid) or {}
        rows.append({
            "id": pid,
            "displayName": pid,
            "driver": str(meta.get("driver") or ""),
            "baseUrl": cred.get("baseUrl") or "",
            "networkScope": "public",
            "billing": str(meta.get("billing") or "metered"),
            "isBuiltin": True,
            "enabled": True,
            "modelCount": counts.get(pid, 0),
            "modelName": (
                grouped_models.get(pid, [{}])[0].get("name")
                or meta.get("default_model")
            ),
            "modelKind": (
                grouped_models.get(pid, [{}])[0].get("modelKind", "chat")
                if grouped_models.get(pid)
                else "chat"
            ),
            "models": grouped_models.get(pid, []),
            "credential": _credential_view(bool(cred.get("hasApiKey")), None),
            "lastProbe": None,
        })
    return rows


def _db_rows(snap: registry_store.RegistrySnapshot) -> list[dict]:
    """DB 快照 → 清单行（`configured` 取「能解密可用」口径，解密失败不算已配置）。

    注意 `_SELECT_PROVIDERS` 只取 `enabled = true`，故此处 `enabled` 恒为 true。
    「列出已停用项」需要另一条不过滤的查询，随 P2 的停用/编辑功能一起做 ——
    不为此改动热路径共用的 SQL（`refresh_registry` 与探测端点都吃它）。
    """
    merged_models = _merged_models(snap)
    counts = _count_models_by_provider(merged_models)
    grouped_models = _models_by_provider(merged_models)
    _decorate_models(
        grouped_models,
        {(str(m.get("provider") or ""), str(m.get("name") or "")) for m in snap.models},
        _roles_by_model(snap),
    )
    rows: list[dict] = []
    for p in snap.providers:
        pid = str(p.get("id") or "")
        resolved = _credential_for(snap, pid)
        # 清单中的 credential 状态专门表示「管理端托管凭据」是否存在；
        # env 凭据可以用于运行时，但不能伪装成已在管理端轮换过。
        credential_configured = pid in snap.credentials and pid in snap.credential_meta
        effective_base_url = (
            str(p.get("base_url") or "")
            or str(getattr(resolved, "base_url", None) or "")
        )
        probe_at = p.get("last_probe_at")
        rows.append({
            "id": pid,
            "displayName": p.get("display_name") or pid,
            "driver": str(p.get("driver") or ""),
            "baseUrl": effective_base_url,
            "networkScope": _normalize_scope(p.get("network_scope")),
            "billing": str(p.get("billing") or "metered"),
            "isBuiltin": bool(p.get("is_builtin")),
            "enabled": bool(p.get("enabled", True)),
            "modelCount": counts.get(pid, 0),
            "modelName": (
                grouped_models.get(pid, [{}])[0].get("name")
                if grouped_models.get(pid)
                else None
            ),
            "modelKind": (
                grouped_models.get(pid, [{}])[0].get("modelKind", "chat")
                if grouped_models.get(pid)
                else "chat"
            ),
            "models": grouped_models.get(pid, []),
            "credential": _credential_view(
                credential_configured, snap.credential_meta.get(pid)
            ),
            "lastProbe": (
                {
                    "at": probe_at.isoformat() if hasattr(probe_at, "isoformat") else str(probe_at),
                    "ok": bool(p.get("last_probe_ok")),
                    "worstGrade": p.get("last_probe_worst_grade"),
                }
                if probe_at is not None
                else None
            ),
        })
    return rows


@router.get("/presets")
async def list_provider_presets(ident=Depends(require_admin_user)) -> dict:
    """供应商预置端点目录（新增/编辑抽屉的厂商·协议·端点候选）。

    静态参考数据，来自 `backend/infra/llm/provider_presets.py`（代码内置，
    不进 DB —— 见设计文档 B.0 的「驱动与厂商能力矩阵留代码」边界）。
    **不参与任何解析链**：它只提供「实例」字段的推荐值，改这里不改运行时行为。

    前端据此把「新增供应商」从「手敲 Base URL」变成
    「选计费计划 → 选厂商·协议 → 自动回填」，避免把按量端点与
    Coding Plan 端点填混（火山引擎 `/api/v3` vs `/api/coding/v3`）。

    `plans` 是「三选一」的计划选项，`billing` 是**派生值**：按设计文档 B.2/B.3，
    Token Plan 与 Coding Plan 都落 `subscription`，只有按量付费落 `metered`。
    """
    return {
        "plans": provider_presets.list_plans(),
        "items": [
            {
                "id": item["id"],
                "plan": item["plan"],
                "vendor": item["vendor"],
                "variant": item["variant"],
                "driver": item["driver"],
                "driverLabel": provider_presets.driver_label(item["driver"]),
                "baseUrl": item["base_url"],
                "apiKeyHint": item["api_key_hint"],
                "note": item["note"],
                "placeholders": list(item["placeholders"]),
            }
            for item in provider_presets.list_presets()
        ],
        "actor": ident.actor,
    }


@router.get("")
async def list_providers(ident=Depends(require_admin_user)) -> dict:
    """供应商清单（tab② 列表数据源）。

    `source` 表明清单来自 `db` 还是代码层 `builtin` 兜底 —— 前端据此在库未就绪时
    提示「配置暂不可用」，而不是让管理员以为自己把供应商删光了。

    `lastProbe` 来自 provider 表中的最近一次探测结果；尚未探测的 provider
    仍返回 `null`，前端按「未验证」灰显（tab⑤ 漂移会点名）。
    """
    snap = await registry_store.load_registry()
    rows, source = (_db_rows(snap), "db") if snap.loaded else (_builtin_rows(), "builtin")
    return {"items": rows, "source": source, "actor": ident.actor}


@router.post("/{provider_id}/verify")
async def verify_saved_provider(
    provider_id: str,
    mode: Literal["fast", "full"] = "fast",
    ident=Depends(require_admin_user),
) -> dict:
    """已存实例复测：默认只测到 L2，完整模式才等待流式 usage。"""
    _enforce_rate_limit(ident.actor, "verify", _VERIFY_PER_MIN)

    snap = await registry_store.load_registry()
    if not snap.loaded:
        raise HTTPException(
            status_code=503,
            detail="注册表不可用（DB 未就绪），无法复测已存实例",
        )

    provider = _find_provider(snap, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"未找到供应商实例：{provider_id}")

    cred = _credential_for(snap, provider_id)
    api_key = (cred.api_key if cred else "") or ""
    base_url = ((cred.base_url if cred else None) or provider.get("base_url") or "")
    extra_headers = ((cred.extra_headers if cred else None)
                     or provider.get("extra_headers") or {})
    scope = _normalize_scope(provider.get("network_scope"))
    driver = provider.get("driver") or models_mod.get_provider_driver(provider_id) or ""
    # 与供应商列表的 modelCount 保持同一口径：内置模型来自代码注册表，
    # 自建/覆盖模型来自 DB；只查 snap.models 会让内置供应商拿到空模型名。
    model_entry = next(
        (m for m in _merged_models(snap) if str(m.get("provider")) == provider_id),
        None,
    )
    model_name = str(model_entry.get("name") or "") if model_entry else ""
    model_kind = models_mod.normalize_model_kind(
        model_entry.get("model_kind") if model_entry else None
    )

    if not base_url:
        raise HTTPException(status_code=422, detail=f"供应商 {provider_id} 未配置 base_url")

    result = await provider_probe.probe_provider(
        driver=driver, base_url=base_url, api_key=api_key,
        model_name=model_name, network_scope=scope,
        extra_headers=extra_headers or None,
        include_stream_usage=(mode == "full"),
        model_kind=model_kind,
    )
    await get_model_config_service().record_probe(provider_id, result.to_dict())
    logger.info(
        "[ProviderProbe] 复测 who=%s provider=%s target=%s scope=%s ok=%s blocked=%s 耗时=%dms",
        ident.actor, provider_id, base_url, scope, result.ok, result.blocked_at,
        sum(s.elapsed_ms for s in result.steps),
    )
    return {
        "provider": provider_id,
        "target": base_url,
        "network_scope": scope,
        "draft": False,
        "mode": mode,
        **result.to_dict(),
    }


@router.post("/verify-draft")
async def verify_draft_provider(
    req: DraftProbeRequest,
    mode: Literal["fast", "full"] = "fast",
    ident=Depends(require_admin_user),
) -> dict:
    """草稿态探测：默认快速确认可调用，完整模式才等待流式 usage。"""
    _enforce_rate_limit(ident.actor, "verify-draft", _DRAFT_VERIFY_PER_MIN)

    scope = _normalize_scope(req.network_scope)
    result = await provider_probe.probe_provider(
        driver=req.driver, base_url=req.base_url, api_key=req.api_key,
        model_name=req.model_name, network_scope=scope,
        include_stream_usage=(mode == "full"),
        model_kind=req.model_kind,
    )
    logger.info(
        "[ProviderProbe] 草稿探测 who=%s target=%s driver=%s scope=%s ok=%s blocked=%s 耗时=%dms",
        ident.actor, req.base_url, req.driver, scope, result.ok, result.blocked_at,
        sum(s.elapsed_ms for s in result.steps),
    )
    return {
        "provider": None,
        "target": req.base_url,
        "network_scope": scope,
        "draft": True,
        "mode": mode,
        **result.to_dict(),
    }
