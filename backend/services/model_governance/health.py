"""模型治理中心 - 健康探活、探针记录与人工状态置失效。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text

from backend.infra.llm import registry_store as _real_registry_store
from backend.memory.database import get_session as _real_get_session
from backend.services.model_governance.base import DependencyProxy, _iso
from backend.shared.logger import logger

get_session = DependencyProxy("get_session", _real_get_session)
registry_store = DependencyProxy("registry_store", _real_registry_store)


async def _invalidate_manual_health(
    session: Any,
    *,
    reason: str,
    model_name: str | None = None,
    provider_id: str | None = None,
) -> None:
    """使受配置变化影响的人工探测结果失效，写入和配置更新共用同一事务。"""
    filters: list[str] = ["probe_source = 'manual'"]
    params: dict[str, str] = {"reason": reason}
    if model_name is not None:
        filters.append("model_name = :model_name")
        params["model_name"] = model_name
    if provider_id is not None:
        filters.append("provider = :provider_id")
        params["provider_id"] = provider_id
    if model_name is None and provider_id is None:
        raise ValueError("失效人工健康结果时必须指定模型或供应商")

    await session.execute(
        text(
            "UPDATE llm_model_health SET status = 'unknown', "
            "last_checked_at = NULL, last_latency_ms = NULL, "
            "last_error = :reason, consecutive_failures = 0, "
            "probe_source = 'scheduled' WHERE " + " AND ".join(filters)
        ),
        params,
    )



def _worst_probe_grade(result: Mapping[str, Any]) -> str | None:
    blocked_at = result.get("blocked_at")
    if blocked_at:
        return str(blocked_at)
    for step in result.get("steps") or []:
        if step.get("status") == "fail":
            return str(step.get("level") or "") or None
    return None



class HealthMixin:
    """模型健康巡检与探测结果管理能力。"""

    async def list_model_health(self) -> list[dict]:
        """全部已探测模型 health 快照（只读缓存，不在线探测）。

        附带供应商 billing 口径（P1 状态口径统一，2026-10-24）：本地计费
        供应商的探测失败要给「确认服务进程已启动或改绑云端」的针对性
        提示，前端与漂移检查共用同一份数据，不再各自解释。
        """
        from backend.infra.llm.health import safe_error_for_status

        rows: list[Mapping[str, Any]] = []
        async for session in get_session():
            result = await session.execute(text(
                "SELECT model_name, provider, model_kind, status, "
                "last_checked_at, last_latency_ms, last_error, "
                "consecutive_failures, probe_source FROM llm_model_health"
            ))
            rows = list(result.mappings())
            break
        billing_by_provider: dict[str, str] = {}
        try:
            snap = await registry_store.load_registry()
            if snap.loaded:
                billing_by_provider = {
                    str(p.get("id") or ""): str(p.get("billing") or "metered")
                    for p in snap.providers
                }
        except Exception:  # noqa: BLE001 — billing 标注失败不阻塞健康快照
            logger.warning("[ModelConfig] 读取供应商 billing 失败，健康行省略该字段")
        out: list[dict] = []
        for r in rows:
            status = str(r["status"] or "unknown")
            provider_id = str(r["provider"] or "")
            out.append({
                "modelName": str(r["model_name"]),
                "provider": provider_id,
                "billing": billing_by_provider.get(provider_id),
                "modelKind": str(r["model_kind"] or "chat"),
                "probeMode": (
                    "availability_only"
                    if str(r["model_kind"] or "chat") in {"vision", "speech"}
                    else "manual_only"
                    if str(r["model_kind"] or "chat") == "ocr"
                    and str(r["probe_source"] or "scheduled") != "manual"
                    else "live_call"
                ),
                "status": status,
                "lastCheckedAt": _iso(r["last_checked_at"]),
                "lastLatencyMs": (
                    int(r["last_latency_ms"])
                    if r["last_latency_ms"] is not None else None
                ),
                # 旧行可能保存过上游原始异常，出 API 前统一转成状态文案。
                "lastError": safe_error_for_status(status),
                "consecutiveFailures": int(r["consecutive_failures"] or 0),
                "probeSource": str(r["probe_source"] or "scheduled"),
            })
        return out


    async def record_probe(self, provider_id: str, result: Mapping[str, Any]) -> None:
        """保存最后一次探测结论；探测接口本身不因审计表故障而失败。"""
        try:
            async for session in get_session():
                await session.execute(
                    text(
                        "UPDATE llm_providers SET last_probe_at = now(), "
                        "last_probe_ok = :ok, last_probe_worst_grade = :worst_grade, "
                        "updated_at = now() WHERE id = :provider_id"
                    ),
                    {
                        "provider_id": provider_id,
                        "ok": bool(result.get("ok")),
                        "worst_grade": _worst_probe_grade(result),
                    },
                )
                await session.commit()
                break
        except Exception:
            logger.warning(
                "[ModelConfig] provider=%s 探测结果落库失败，不影响探测响应",
                provider_id,
                exc_info=True,
            )

