"""admin_email.py — 邮件通道状态 API（管理端「审批中心」通道卡片数据源）

只读端点：汇总邮件通道当前可用性与缺口，不暴露任何凭据明文
（密码只给 has_password 布尔）。消费方 = 管理端 /approvals 页顶部的
通道健康卡片——邮件发送（email.send）与未来的定时报告推送都走这条通道，
配没配好、缺什么，必须在页面上看得见，而不是上服务器翻 .env。

口径（全部派生，G2 禁手抄）：
- enabled 判定与 tools/email.py 的发送侧降级逻辑同源：
  smtp 引擎 = SMTP_USER/PASSWORD 齐备；agently 引擎 = AGENTLY_BIN 可执行。
- periodic_tasks 从 celery beat_schedule 派生 crontab 型周期任务
  （秒级扫描类排除）——它们是「报告/日报邮件推送」的候选消费方；
  当前无任务真实接线邮件推送，页面如实展示候选清单。
"""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/admin/email", tags=["邮件通道"])


def _smtp_status() -> dict:
    from backend.config import (
        EMAIL_ENGINE, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER,
    )

    return {
        "engine_selected": EMAIL_ENGINE == "smtp",
        "host": SMTP_HOST,
        "port": SMTP_PORT,
        "from_addr": SMTP_FROM,
        "user": SMTP_USER,
        "has_password": bool(SMTP_PASSWORD),
        "complete": bool(SMTP_USER and SMTP_PASSWORD),
    }


def _agently_status() -> dict:
    import shutil

    from backend.config import AGENTLY_BIN, EMAIL_ENGINE

    cli_path = shutil.which(AGENTLY_BIN)
    return {
        "engine_selected": EMAIL_ENGINE == "agently",
        "bin": AGENTLY_BIN,
        "cli_installed": cli_path is not None,
        "cli_path": cli_path or "",
        # OAuth 登录态 CLI 侧才能回答，最小版只探可执行性；
        # cli 在而 OAuth 过期的症状会在真实发送时报错并在 trace 可见
        "complete": cli_path is not None,
    }


@router.get("/channel")
async def email_channel(request: Request):
    """邮件通道状态：引擎/配置完整度/审批门/周期任务候选（只读，不含凭据明文）。"""
    from backend.config import EMAIL_ENGINE, TOOL_APPROVAL_MODE

    smtp = _smtp_status()
    agently = _agently_status()
    engine_ready = smtp["complete"] if EMAIL_ENGINE == "smtp" else agently["complete"]
    if EMAIL_ENGINE == "smtp" and not smtp["complete"]:
        reason = "未配置 SMTP 凭据（SMTP_USER / SMTP_PASSWORD 为空）——发送会返回 [EMAIL DISABLED] 降级提示"
    elif EMAIL_ENGINE == "agently" and not agently["complete"]:
        reason = f"agently 引擎已选择但 {agently['bin']} 不可执行（未安装或不在 PATH）"
    else:
        reason = ""

    # 周期任务候选：beat_schedule 中 crontab 型（分钟级轮询/扫描类排除）——
    # 这些是「定时报告邮件推送」的潜在消费方；当前均未接线邮件推送
    periodic: list[dict] = []
    try:
        from backend.tasks.celery_app import celery_app

        for name, entry in (celery_app.conf.beat_schedule or {}).items():
            sched = entry.get("schedule")
            if not hasattr(sched, "hour"):  # crontab 才有 hour 属性；float=秒间隔
                continue
            periodic.append({
                "task": entry.get("task", name),
                "name": name,
                "cron": f"{getattr(sched, '_orig_minute', '*')} {getattr(sched, '_orig_hour', '*')} * * *",
            })
    except Exception:  # pragma: no cover — beat 未加载等边界，软失败
        periodic = []

    return {
        "engine": EMAIL_ENGINE,
        "enabled": engine_ready,
        "degraded_reason": reason,
        "approval_mode": TOOL_APPROVAL_MODE,
        "smtp": smtp,
        "agently": agently,
        "capabilities": {
            "send": True,
            "search_read_watch": EMAIL_ENGINE == "agently",
            "note": "search/read/watch 仅 agently 引擎提供（需完成 QQ 邮箱 OAuth）",
        },
        "periodic_tasks": sorted(periodic, key=lambda t: t["task"]),
        "periodic_note": "以下 crontab 周期任务是「报告/日报邮件推送」的候选消费方（当前均未接线邮件发送）",
    }
