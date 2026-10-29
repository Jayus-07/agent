"""app/api/routes/automations.py — 用户自动任务 API（决策 #27）

三个 router（挂载见 app/api/router.py）：
- webhook_router  POST /webhooks/automations/{tenant_id}/{automation_id}
    外部系统回调入口。认证 = HMAC-SHA256 签名（不经 gateway-auth，见
    apisix/apisix.yaml /webhooks/* 路由），安全三件套：
      1) X-Signature 验签（HMAC-SHA256(raw_body, 解密密钥)，恒定时间比较，
         401 统一文案不回显失败细节）
      2) X-Event-Id 去重（唯一索引命中 → 200 + skipped_duplicate，不重派）
      3) 租户绑定（automation.tenant_id 必须 == 路径 tenant_id，不匹配 404
         不泄露存在性；路径含租户段即"路径来源"）
- router          /automations  用户端 CRUD + 事件历史（只操作自己的行）
- admin_router    /admin/automations  管理端总览与停用开关（require_admin_user）

执行链复用（红线）：webhook/cron 到期只写事件并入队 automations.run
（Celery 壳），真正执行 = task_service.create_task + task_manager.enqueue_task
→ tasks.execute_agent 既有管线；写操作由既有 Governance/确认门兜底。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.app.api.identity import resolve_identity
from backend.services import automations
from backend.shared.logger import logger

# 统一 401 文案：不区分密钥缺失/签名错误，不给枚举与调试信息
_WEBHOOK_401 = "签名校验失败"
# 统一 404 文案：不存在 / 租户不匹配 / 已删除同一文案，不泄露存在性
_WEBHOOK_404 = "资源不存在"


# ═══════════════════════════════════════════════════
# Webhook 回调（外部系统，无用户身份）
# ═══════════════════════════════════════════════════

webhook_router = APIRouter(prefix="/webhooks/automations", tags=["自动任务-Webhook"])


@webhook_router.post("/{tenant_id}/{automation_id}")
async def webhook_trigger(tenant_id: str, automation_id: str,
                          request: Request) -> dict:
    """业务事件回调 → 验签 → 去重 → 事件行 + 派发 automations.run。"""
    from backend.tasks.queue_router import beat_queue

    raw_body = await request.body()
    signature = request.headers.get("X-Signature", "")
    external_event_id = (request.headers.get("X-Event-Id") or "").strip()

    automation = automations.get_automation(automation_id, internal=True)
    # 租户绑定不匹配与不存在同响应（404，不泄露存在性）
    if automation is None or automation["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail=_WEBHOOK_404)
    if not automation["enabled"]:
        # 停用行为对回调方保持 404 语义：不确认存在性、不消耗事件
        raise HTTPException(status_code=404, detail=_WEBHOOK_404)

    secret = automations.get_webhook_secret(automation)
    if not automations.verify_webhook_signature(secret, raw_body, signature):
        logger.warning("[AutomationsWebhook] %s 签名校验失败（tenant=%s）",
                       automation_id, tenant_id)
        raise HTTPException(status_code=401, detail=_WEBHOOK_401)

    # 去重：X-Event-Id 唯一索引（同键二次回调 → skipped_duplicate）
    if external_event_id:
        event_id = automations.record_event(
            automation_id, source="webhook",
            external_event_id=external_event_id,
            payload={"body_bytes": len(raw_body)},
            status="queued")
        if event_id is None:
            return {"status": "skipped_duplicate"}
    else:
        # 无 X-Event-Id：不去重（每次回调都触发），事件行 external_event_id 留空
        event_id = automations.record_event(
            automation_id, source="webhook",
            payload={"body_bytes": len(raw_body)}, status="queued")

    try:
        from backend.tasks.automation_tasks import run_user_automation

        run_user_automation.apply_async(
            args=[automation_id, event_id],
            queue=beat_queue("automations.run"))
    except Exception as e:  # noqa: BLE001 — broker 不可达：事件明确失败，不装成功
        logger.error("[AutomationsWebhook] %s 派发失败: %s", automation_id, e)
        automations.mark_event(event_id, status="failed",
                               detail=f"执行链派发失败: {e}"[:2000])
        raise HTTPException(status_code=503, detail="任务队列暂不可用，请稍后重试")

    return {"status": "queued", "event_id": event_id}


# ═══════════════════════════════════════════════════
# 用户端 CRUD（身份强制取当前用户；只操作自己的行）
# ═══════════════════════════════════════════════════

router = APIRouter(prefix="/automations", tags=["自动任务"])


class AutomationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    trigger_type: str = Field("cron", description="cron | webhook")
    cron_expr: str = Field("", description="5 字段 cron（UTC），trigger_type=cron 必填")
    payload: dict = Field(..., description='{"question": "纯问题文本"}（v1）')
    confirm_policy: str = Field("auto", description="v1 仅 auto")


class AutomationUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    cron_expr: str | None = None
    payload: dict | None = None
    enabled: bool | None = None


def _user_identity(request: Request) -> tuple[str, str]:
    """当前用户 + 租户（服务端身份头权威；未认证 401）。

    用户自动任务是用户级资源：user_id 一律取服务端验证后的身份，请求体
    身份字段不参与（防伪造）；legacy 模式下 resolve_identity 会取 body，
    本路由不透传 body_user_id，保持身份源唯一。
    """
    ident = resolve_identity(request)
    if not ident.user_id:
        raise HTTPException(status_code=401, detail="未认证：自动任务接口要求用户身份")
    tenant_id = ident.tenant_id or "default"
    return ident.user_id, tenant_id


def _own_automation(automation_id: str, user_id: str, tenant_id: str) -> dict:
    record = automations.get_automation(automation_id,
                                        user_id=user_id, tenant_id=tenant_id)
    if record is None:
        # 不区分 404/403：避免横向枚举探测（隔离即不暴露存在性）
        raise HTTPException(status_code=404, detail="自动任务不存在")
    return record


def _present(record: dict) -> dict:
    """用户端出参：webhook 类型附回调地址与租户绑定信息；永不含密钥。"""
    out = dict(record)
    if record["trigger_type"] == "webhook":
        out["webhook_url"] = automations.webhook_url(
            record["tenant_id"], record["id"])
        out["signature_header"] = "X-Signature"
        out["event_id_header"] = "X-Event-Id"
    return out


@router.get("")
async def list_my_automations(request: Request) -> dict[str, Any]:
    user_id, tenant_id = _user_identity(request)
    items = automations.list_automations(user_id=user_id, tenant_id=tenant_id)
    return {"automations": [_present(i) for i in items], "total": len(items)}


@router.post("", status_code=201)
async def create_my_automation(request: Request,
                               body: AutomationCreateRequest) -> dict:
    user_id, tenant_id = _user_identity(request)
    try:
        record, secret = automations.create_automation(
            user_id=user_id, tenant_id=tenant_id, name=body.name,
            trigger_type=body.trigger_type, cron_expr=body.cron_expr,
            payload=body.payload, confirm_policy=body.confirm_policy)
    except automations.AutomationValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # UNIQUE(user_id, name, tenant_id) 冲突等落库错误
        if "uq_user_automations_identity" in str(e):
            raise HTTPException(status_code=409,
                                detail="同名自动任务已存在")
        raise
    out = _present(record)
    # 明文密钥只在创建响应返回一次（照凭据中心模式；落库为 Fernet 密文）
    if secret:
        out["webhook_secret"] = secret
    logger.info("[Automations] 用户 %s 创建自动任务 %s (%s)",
                user_id, record["id"], body.trigger_type)
    return out


@router.put("/{automation_id}")
async def update_my_automation(automation_id: str, request: Request,
                               body: AutomationUpdateRequest) -> dict:
    user_id, tenant_id = _user_identity(request)
    _own_automation(automation_id, user_id, tenant_id)  # 先做存在性/归属校验
    try:
        record = automations.update_automation(
            automation_id, user_id=user_id, tenant_id=tenant_id,
            name=body.name, cron_expr=body.cron_expr,
            payload=body.payload, enabled=body.enabled)
    except automations.AutomationValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if record is None:
        raise HTTPException(status_code=404, detail="自动任务不存在")
    return _present(record)


@router.delete("/{automation_id}")
async def delete_my_automation(automation_id: str, request: Request) -> dict:
    user_id, tenant_id = _user_identity(request)
    if not automations.delete_automation(automation_id,
                                         user_id=user_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="自动任务不存在")
    return {"deleted": True, "id": automation_id}


@router.get("/{automation_id}/events")
async def list_my_automation_events(automation_id: str, request: Request,
                                    limit: int = 50) -> dict[str, Any]:
    user_id, tenant_id = _user_identity(request)
    _own_automation(automation_id, user_id, tenant_id)  # 归属校验（隔离强制）
    return {"events": automations.list_events(automation_id, limit=limit)}


# ═══════════════════════════════════════════════════
# 管理端：总览与开关（跨租户只读 + 停用）
# ═══════════════════════════════════════════════════

admin_router = APIRouter(prefix="/admin/automations", tags=["Admin-自动任务"])


@admin_router.get("/overview")
async def admin_automation_overview(
        _operator: OperatorIdentity = Depends(require_admin_user)) -> dict:
    """跨租户计数 + 最近失败事件 + 全量清单（管理端总览数据源）。"""
    overview = automations.admin_overview()
    overview["automations"] = automations.admin_list_automations(limit=100)
    return overview


@admin_router.patch("/{automation_id}")
async def admin_set_automation_enabled(
        automation_id: str,
        body: dict = Body(...),
        _operator: OperatorIdentity = Depends(require_admin_user)) -> dict:
    """管理端停用/启用开关（enabled 布尔；触发源治理入口）。"""
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled 必须为布尔")
    if not automations.set_enabled(automation_id, enabled=enabled):
        raise HTTPException(status_code=404, detail="自动任务不存在")
    logger.info("[Automations] 管理端%s自动任务 %s",
                "启用" if enabled else "停用", automation_id)
    return {"updated": True, "id": automation_id, "enabled": enabled}
