"""routes/sys_config_admin.py — 灰度开关动态配置管理端点（2026-09-16 Lite 版）

- GET /sys/config           全部登记开关的生效状态（来源 db / env-default）
- PUT /sys/config/{key}     覆盖写入 + 审计历史（sys_config_history：旧值→新值）

安全边界（与 /sys/security/* 同策略）：
- 统一挂 require_admin_user（kind==user 且 role==admin），服务凭据通道不开放；
- /sys 前缀在 api_key_middleware 白名单，鉴权完全由本依赖承担；
- 白名单校验在 service 层（键登记表 + 值 allowed 集合），非法值 400。

审计闭环：[SysConfigAudit] 结构化日志（who/key/old/new）+ sys_config_history
表（可查回滚基准）+ 网关访问日志（HTTP 层 who/when）。

网关映射：APISIX 剥 /api 前缀 → 对外 /api/sys/config/*。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.services import sys_config

router = APIRouter(prefix="/sys/config", tags=["系统配置"])


class ConfigUpdateRequest(BaseModel):
    value: str = Field(..., max_length=128, description="新值（须在白名单内）")


@router.get("")
async def get_config(operator: OperatorIdentity = Depends(require_admin_user)):
    """全部登记开关的生效状态（只读；含当前来源与最近修改人）。"""
    items = await sys_config.list_effective()
    return {"items": items, "actor": operator.actor}


@router.put("/{key}")
async def update_config(key: str, body: ConfigUpdateRequest, request: Request,
                        operator: OperatorIdentity = Depends(require_admin_user)):
    """覆盖写入守卫开关（免重启生效，本实例即时、其他实例 ≤1 个 TTL）。

    回滚 = 把旧值写回（历史表 sys_config_history 与响应体均携带旧值）。
    """
    try:
        result = await sys_config.set_value(key, body.value, operator.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未登记的配置键: {key}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"key": key, **result, "changedBy": operator.actor}
