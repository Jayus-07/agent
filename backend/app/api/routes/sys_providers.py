"""sys_providers.py — LLM 供应商清单与连通性探测端点（P1b + P2 数据源）

| 端点 | 用途 | 限流 |
|---|---|---|
| `GET  /sys/providers` | 供应商清单（tab② 列表数据源，只读；B3 起 JWT 用户可读，editor 只读可见） | — |
| `POST /sys/providers/{provider_id}/verify` | 已存实例复测（默认快速，`mode=full` 才检查流式 usage） | admin · 10 次/分 |
| `POST /sys/providers/verify-draft` | 草稿态探测（body 带 driver/base_url/apiKey/scope） | admin · **5 次/分**（更严） |
| `POST /sys/providers/model-catalog` | 草稿态**模型目录**（填模型名时的可选清单） | admin · 20 次/分 |
| `POST /sys/providers/{provider_id}/model-catalog` | 已存实例模型目录 | admin · 20 次/分 |

**探测与「模型目录」是两件事**（2026-09-21 分层重划，见 `provider_probe` 模块头）：

- `verify*` = **判定**（L0 安全闸门 + L2 真实最小调用）。只有 L2 能回答「能不能用」。
- `model-catalog` = **填料**（拉 `GET {base}/models`，只回报模型名，供下拉选择）。
  它**不参与任何判定**：拿不到清单只意味着用户要手打模型名，**不是**「供应商不可用」。
  原 L1 曾挂在探测链上，既拖慢成功路径（串行 8s 超时），又让「拿不到清单」容易被误读
  成「测试失败」，故移出为独立按需端点（服务端带短 TTL 缓存，见 `provider_probe`）。

四个探测/目录端点落实 B.6 的**四道限制**（否则它就是一个「任意 URL 探测代理」）：

1. **admin only** —— 复用 `require_admin_user`（kind=user + role=admin）。
   **例外**：`GET /sys/providers` 清单读端点自 2026-09-21（B3）起放宽为
   `require_user_actor`（任意 JWT 用户身份）—— editor 需要在「供应商与密钥」
   tab 只读浏览模型占用情况（角色徽标跳转改绑的入口）。写/探测/目录端点不变；
   service / API-Key 身份仍被敏感 guard 拦截（kind 门槛未动）。
2. **目标过 `url_guard`** —— 私网只在实例 `network_scope='private'` 时放行，
   且该值来自 DB 显式勾选，不由「解析结果」推导（防 DNS rebinding）。目录端点同样
   会带着密钥出站，因此**走同一道闸门**，不因为是「只读」就放宽。
3. **报文固定** —— 探测 body 只有 driver/base_url/api_key/model_name/network_scope，
   不接受自定义 prompt / body / 任意 header；目录 body 更窄，只有 base_url/api_key/scope。
   探测用固定 prompt 与 `max_tokens=16`。默认快速模式只跑到 L2，`mode=full` 才检查流式 usage。
4. **限流** —— 进程内滑动窗口，按 actor 分桶；草稿态探测配额更小。

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

from backend.app.api.deps import require_admin_user, require_user_actor
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm import registry_store
from backend.services import provider_probe
from backend.services.model_config import get_model_config_service
from backend.shared.logger import logger

router = APIRouter(prefix="/sys/providers", tags=["sys-providers"])

_VERIFY_PER_MIN = 10
_DRAFT_VERIFY_PER_MIN = 5
# 目录比探测宽松：它有服务端短 TTL 缓存，且用户可能连点几次换地址试。
# 但它同样会带着密钥出站，所以仍然限流、仍然过 url_guard。
_CATALOG_PER_MIN = 20
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
    # 已登记供应商的草稿测试（如供应商卡片下的「新增模型」弹窗）：留空
    # apiKey 并传 providerId → 服务端取托管凭据测试，明文不下发前端
    #（§7.3 硬约束 1）。
    provider_id: str | None = Field(None, alias="providerId", max_length=128)


class ModelCatalogRequest(BaseModel):
    """草稿态模型目录请求。字段比探测更窄 —— 目录只是一个只读 POST。

    没有 `driver`：`/models` 是标准 HTTP GET + Bearer，与协议无关。
    """

    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field(
        ..., alias="baseUrl", min_length=1, description="供应商端点根地址"
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


def _specialized_probe_base_url(
    snap: registry_store.RegistrySnapshot, provider_id: str, model_kind: str
) -> str:
    """embedding/rerank 优先取专项绑定表里运行时真实在用的协议地址。

    供应商表保存的是通用根地址；同一供应商下 embedding（OpenAI 兼容
    `/compatible-mode/v1`）与 rerank（DashScope 原生 `/api/v1`）协议可能不同，
    任何单一根地址都无法同时作为两者的探测目标。专项绑定表
    （`llm_specialized_model_bindings`）按角色保存带协议路径的 URL，是运行时
    真实在用的地址 —— 探测必须与之同源，否则会对裸根地址拼协议路径打出 404，
    再把 404 误归因成「模型名错误或 Key 无权访问」（2026-09-22 实测事故）。
    """
    if model_kind not in {"embedding", "rerank"}:
        return ""
    row = snap.specialized.get(model_kind) or {}
    if str(row.get("provider_id") or "") != provider_id:
        return ""
    return str(row.get("base_url") or "").strip().rstrip("/")


def _count_models_by_provider(entries) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in entries:
        pid = str(m.get("provider") or "")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _models_by_provider(entries) -> dict[str, list[dict]]:
    """按供应商返回模型目录，并补齐历史条目的用途类型与单价（USD/1M）。"""
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
            "inputPrice": item.get("input_price_per_1m"),
            "outputPrice": item.get("output_price_per_1m"),
            "cachedInputPrice": item.get("cached_input_price_per_1m"),
            "priceCurrency": item.get("price_currency") or "USD",
            "upstreamName": item.get("upstream_name") or "",
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

    §B.15（迁移 0023）起清单为 **DB-only**：进入本函数的条目全部来自
    `llm_models` 表，一律可移除（`source='user'`），判定只剩角色占用。
    `db_keys` 参数保留只为兼容 `_builtin_rows` 的 env 兜底分支（该分支
    没有 DB 行，但代码层种子已退役，同样不会产生 builtin 模型）。
    - `usedByRoles`：该模型正被哪些角色占用。**放在列表里而不是等到 409 才知道**，
      是为了让「移除」按钮能就地禁用并说明原因，而不是让用户点完再被拒。
    """
    for pid, items in grouped.items():
        for item in items:
            name = str(item.get("name") or "")
            item["source"] = "user"
            item["usedByRoles"] = sorted(roles_by_model.get(name, []))


def _roles_by_model(snap: registry_store.RegistrySnapshot) -> dict[str, list[str]]:
    """role → 模型名 反转为 模型名 → [role]（快照已带 roles，无需额外查询）。"""
    inverted: dict[str, list[str]] = {}
    for role, model_name in (snap.roles or {}).items():
        inverted.setdefault(str(model_name), []).append(str(role))
    return inverted


def _merged_models(snap: registry_store.RegistrySnapshot) -> list[dict]:
    """DB 模型清单（§B.15 起 DB-only，代码层种子已随迁移 0023 退役）。"""
    return [
        item for item in snap.models
        if item.get("provider") and item.get("name")
    ]


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


# `_builtin_rows`（DB 不可用时的代码层厂商兜底）已于 2026-09-22 退役：
# 内置供应商特殊类不再存在，DB 未就绪时清单返回空列表 + `source='builtin'`，
# 由前端亮「DB 未就绪」横幅，不再伪装出厂商行。


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
            # 附加请求体（087 迁移）：管理端据此回显/编辑「关闭思考」等字段
            "extraBody": dict(p.get("extra_body") or {}),
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


@router.get("")
async def list_providers(ident=Depends(require_user_actor)) -> dict:
    """供应商清单（tab② 列表数据源）。

    权限：B3（2026-09-21）起由 admin-only 放宽为 `require_user_actor` ——
    editor 只读可见（角色占用徽标要跳「模型角色」页改绑，得先能看到清单）。
    与 `GET /sys/model-roles` 同档；service / API-Key 身份仍被拦。

    `source` 表明清单来自 `db` 还是代码层 `builtin` 兜底 —— 前端据此在库未就绪时
    提示「配置暂不可用」，而不是让管理员以为自己把供应商删光了。
    2026-09-22 拍板：内置供应商特殊类退役，`_builtin_rows` 兜底清空 ——
    DB 未就绪时列表就是空的（`source='builtin'` 仍告知前端亮「DB 未就绪」横幅），
    不再把代码层厂商伪装成清单数据。

    `lastProbe` 来自 provider 表中的最近一次探测结果；尚未探测的 provider
    仍返回 `null`，前端按「未验证」灰显（tab⑤ 漂移会点名）。
    """
    snap = await registry_store.load_registry()
    rows, source = (_db_rows(snap), "db") if snap.loaded else ([], "builtin")
    return {"items": rows, "source": source, "actor": ident.actor}


@router.post("/{provider_id}/verify")
async def verify_saved_provider(
    provider_id: str,
    mode: Literal["fast", "full"] = "fast",
    model_name: str = "",
    ident=Depends(require_admin_user),
) -> dict:
    """已存实例复测：默认只测到 L2，完整模式才等待流式 usage。

    `model_name` 指定要测的模型（模型级测试按钮）。不传时取该供应商名下
    第一个模型（历史行为，兼容旧入口）。embedding/rerank 的探测地址优先取
    专项绑定表里的协议完整 URL（`_specialized_probe_base_url`），取不到再退
    供应商根地址 —— 响应里的 `model` / `target` 说明这次测的是谁、打的哪。
    """
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
    extra_headers = ((cred.extra_headers if cred else None)
                     or provider.get("extra_headers") or {})
    scope = _normalize_scope(provider.get("network_scope"))
    driver = provider.get("driver") or models_mod.get_provider_driver(provider_id) or ""
    # 与供应商列表的 modelCount 保持同一口径（§B.15 起均为 DB-only）：
    # 只查 snap.models 即可，代码层种子已随迁移 0023 退役。
    provider_models = [
        m for m in _merged_models(snap) if str(m.get("provider")) == provider_id
    ]
    if model_name.strip():
        target = model_name.strip()
        model_entry = next(
            (m for m in provider_models if str(m.get("name") or "") == target),
            None,
        )
        if model_entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"供应商 {provider_id} 下没有模型：{target}",
            )
    else:
        model_entry = next(iter(provider_models), None)
    resolved_model_name = str(model_entry.get("name") or "") if model_entry else ""
    # 登记名/上游名拆分（2026-09-22）：探测发上游 API 的 model 用上游名
    resolved_upstream_name = (
        str(model_entry.get("upstream_name") or "").strip()
        if model_entry else ""
    ) or resolved_model_name
    model_kind = models_mod.normalize_model_kind(
        model_entry.get("model_kind") if model_entry else None
    )

    base_url = (
        _specialized_probe_base_url(snap, provider_id, model_kind)
        or ((cred.base_url if cred else None) or provider.get("base_url") or "")
    )

    if not base_url:
        raise HTTPException(status_code=422, detail=f"供应商 {provider_id} 未配置 base_url")

    result = await provider_probe.probe_provider(
        driver=driver, base_url=base_url, api_key=api_key,
        model_name=resolved_model_name, network_scope=scope,
        extra_headers=extra_headers or None,
        include_stream_usage=(mode == "full"),
        model_kind=model_kind,
        upstream_model_name=resolved_upstream_name,
    )
    await get_model_config_service().record_probe(provider_id, result.to_dict())
    logger.info(
        "[ProviderProbe] 复测 who=%s provider=%s model=%s target=%s scope=%s "
        "ok=%s blocked=%s 耗时=%dms",
        ident.actor, provider_id, resolved_model_name, base_url, scope,
        result.ok, result.blocked_at,
        sum(s.elapsed_ms for s in result.steps),
    )
    return {
        "provider": provider_id,
        "model": resolved_model_name,
        "model_kind": model_kind,
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
    api_key = req.api_key
    if not api_key and req.provider_id:
        # 已登记供应商的草稿测试：取托管凭据（明文不回显）
        snap0 = await registry_store.load_registry()
        resolved = _credential_for(snap0, req.provider_id)
        api_key = getattr(resolved, "api_key", None) or ""
    if not api_key and req.driver != "ollama":
        raise HTTPException(
            status_code=422,
            detail="请输入 API Key，或传入已登记供应商的 providerId 以使用托管凭据",
        )
    result = await provider_probe.probe_provider(
        driver=req.driver, base_url=req.base_url, api_key=api_key,
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


# ── 模型目录（填写辅助，不参与任何判定）──────────────────────────────────
#
# 这两个端点回答的是「这个地址上有哪些模型名可以填」，而不是「这个供应商能不能用」。
# 所以它们**永远不返回 ok/blocked_at 那套判定语义**，失败也只影响「能不能帮你省掉
# 手打」。前端把结果渲染在「模型名称」输入框旁的下拉里，用户点了才请求。


@router.post("/model-catalog")
async def model_catalog_draft(
    req: ModelCatalogRequest,
    ident=Depends(require_admin_user),
) -> dict:
    """草稿态模型目录：用用户刚填的地址与 Key 去拉一次可选模型名。"""
    _enforce_rate_limit(ident.actor, "catalog", _CATALOG_PER_MIN)

    scope = _normalize_scope(req.network_scope)
    catalog = await provider_probe.fetch_model_catalog(
        req.base_url, req.api_key,
        allow_private=(scope == "private"),
    )
    logger.info(
        "[ModelCatalog] 草稿目录 who=%s target=%s scope=%s ok=%s cached=%s 条数=%d",
        ident.actor, req.base_url, scope, catalog.ok, catalog.cached,
        len(catalog.items),
    )
    return {
        "provider": None,
        "target": req.base_url,
        "network_scope": scope,
        "draft": True,
        **catalog.to_dict(),
    }


@router.post("/{provider_id}/model-catalog")
async def model_catalog_saved(
    provider_id: str,
    ident=Depends(require_admin_user),
) -> dict:
    """已存实例的模型目录：用库里已保存的地址与密钥去拉（密钥不回显）。"""
    _enforce_rate_limit(ident.actor, "catalog", _CATALOG_PER_MIN)

    snap = await registry_store.load_registry()
    if not snap.loaded:
        raise HTTPException(
            status_code=503,
            detail="注册表不可用（DB 未就绪），无法读取供应商地址",
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

    if not base_url:
        raise HTTPException(status_code=422, detail=f"供应商 {provider_id} 未配置 base_url")

    catalog = await provider_probe.fetch_model_catalog(
        base_url, api_key,
        extra_headers=extra_headers or None,
        allow_private=(scope == "private"),
    )
    logger.info(
        "[ModelCatalog] 实例目录 who=%s provider=%s target=%s scope=%s ok=%s cached=%s 条数=%d",
        ident.actor, provider_id, base_url, scope, catalog.ok, catalog.cached,
        len(catalog.items),
    )
    return {
        "provider": provider_id,
        "target": base_url,
        "network_scope": scope,
        "draft": False,
        **catalog.to_dict(),
    }
