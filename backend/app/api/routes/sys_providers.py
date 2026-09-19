"""sys_providers.py — LLM 供应商清单与连通性探测端点（P1b + P2 数据源）

| 端点 | 用途 | 限流 |
|---|---|---|
| `GET  /sys/providers` | 供应商清单（tab② 列表数据源，只读） | — |
| `POST /sys/providers/{provider_id}/verify` | 已存实例复测（不带 body） | admin · 10 次/分 |
| `POST /sys/providers/verify-draft` | 草稿态探测（body 带 driver/base_url/apiKey/scope） | admin · **5 次/分**（更严） |

后两个端点落实 B.6 的**四道限制**（否则它就是一个「任意 URL 探测代理」）：

1. **admin only** —— 复用 `require_admin_user`（kind=user + role=admin）。
2. **目标过 `url_guard`** —— 私网只在实例 `network_scope='private'` 时放行，
   且该值来自 DB 显式勾选，不由「解析结果」推导（防 DNS rebinding）。
3. **报文固定** —— body 只有 driver/base_url/api_key/model_name/network_scope，
   不接受自定义 prompt / body / 任意 header；探测用固定 prompt 与 `max_tokens=16`。
4. **限流** —— 进程内滑动窗口，按 actor 分桶；草稿态配额更小。

响应一律**裸 dict**（§1.1.1 决策，无 Result 壳）。审计走 logger：
**who / 目标 URL / network_scope / 分级结论 / 耗时，不记密钥**（B.6）。

⚠️ 本路由**尚未注册**到 `api_router` —— `app/api/router.py` 当前被并发会话
持有未提交改动（含 3 个未提交模块的 include），提交它会连带让主干 import
失败。待该文件落定后补一行 `include_router(sys_providers.router)` 即可生效。
"""
from __future__ import annotations

import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import require_admin_user
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm import registry_store
from backend.services import provider_probe
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

    driver: str = Field(..., min_length=1, description="openai / anthropic / ollama")
    base_url: str = Field(..., min_length=1, description="供应商端点根地址")
    model_name: str = Field(..., min_length=1, description="用于验证的模型名")
    api_key: str = Field("", description="草稿密钥；ollama 可留空")
    network_scope: str = Field("public", description="public | private")


def _normalize_scope(value: str | None) -> str:
    return "private" if (value or "").strip().lower() == "private" else "public"


def _find_provider(snap: registry_store.RegistrySnapshot, provider_id: str) -> dict | None:
    return next((p for p in snap.providers if str(p.get("id")) == provider_id), None)


def _credential_for(snap: registry_store.RegistrySnapshot, provider_id: str):
    """优先取 DB 凭据；回退统一入口（env 兜底）。

    注意：`resolve_credentials` 对不在代码层注册表里的 provider 会抛
    `UnknownProviderError`（自建 provider 属此列），故必须先查快照。
    """
    for key, cred in snap.credentials.items():
        if str(key) == provider_id:
            return cred
    try:
        return credentials_mod.resolve_credentials(provider_id)
    except Exception:  # noqa: BLE001 — 未知 provider／无 env 绑定都按「无凭据」处理
        return None


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
    counts = _count_models_by_provider(snap.models)
    rows: list[dict] = []
    for p in snap.providers:
        pid = str(p.get("id") or "")
        rows.append({
            "id": pid,
            "displayName": p.get("display_name") or pid,
            "driver": str(p.get("driver") or ""),
            "baseUrl": str(p.get("base_url") or ""),
            "networkScope": _normalize_scope(p.get("network_scope")),
            "billing": str(p.get("billing") or "metered"),
            "isBuiltin": bool(p.get("is_builtin")),
            "enabled": bool(p.get("enabled", True)),
            "modelCount": counts.get(pid, 0),
            "credential": _credential_view(
                pid in snap.credentials, snap.credential_meta.get(pid)
            ),
            "lastProbe": None,
        })
    return rows


@router.get("")
async def list_providers(ident=Depends(require_admin_user)) -> dict:
    """供应商清单（tab② 列表数据源）。

    `source` 表明清单来自 `db` 还是代码层 `builtin` 兜底 —— 前端据此在库未就绪时
    提示「配置暂不可用」，而不是让管理员以为自己把供应商删光了。

    `lastProbe` 恒为 `null`：探测结果持久化表尚未建立（0018），当前只有内存态；
    前端按「未验证」灰显（tab⑤ 漂移会点名）。
    """
    snap = await registry_store.load_registry()
    rows, source = (_db_rows(snap), "db") if snap.loaded else (_builtin_rows(), "builtin")
    return {"items": rows, "source": source, "actor": ident.actor}


@router.post("/{provider_id}/verify")
async def verify_saved_provider(
    provider_id: str, ident=Depends(require_admin_user)
) -> dict:
    """已存实例复测：凭据从注册表/环境解析，不带请求 body。"""
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
    driver = models_mod.get_provider_driver(provider_id) or provider.get("driver") or ""
    model_name = next(
        (m["name"] for m in snap.models if str(m.get("provider")) == provider_id), ""
    )

    if not base_url:
        raise HTTPException(status_code=422, detail=f"供应商 {provider_id} 未配置 base_url")

    result = await provider_probe.probe_provider(
        driver=driver, base_url=base_url, api_key=api_key,
        model_name=model_name, network_scope=scope,
        extra_headers=extra_headers or None,
    )
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
        **result.to_dict(),
    }


@router.post("/verify-draft")
async def verify_draft_provider(
    req: DraftProbeRequest, ident=Depends(require_admin_user)
) -> dict:
    """草稿态探测：「保存前先测」，避免「填完保存了才知道不能用」（B.4 硬约束 5）。"""
    _enforce_rate_limit(ident.actor, "verify-draft", _DRAFT_VERIFY_PER_MIN)

    scope = _normalize_scope(req.network_scope)
    result = await provider_probe.probe_provider(
        driver=req.driver, base_url=req.base_url, api_key=req.api_key,
        model_name=req.model_name, network_scope=scope,
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
        **result.to_dict(),
    }
