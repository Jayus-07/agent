"""
alerts.py — 告警与可观测性

PlanAlert 数据类 + 告警代码表 + 降级日志写入 + webhook 外推（P1-8）。
贯穿 Planner / Critique / Supervisor / Worker / Reporter 全链路。

外推通道（P1-8）：
  - 本地 JSONL：logs/degradation.jsonl（原有行为，问题排查用）
  - webhook：ALERT_WEBHOOK_URL 配置后，warn/error 级别告警推送到
    企微/钉钉/飞书群机器人或任意自建接收端（POST JSON）
  - Prometheus：degradation_alerts_total{code, level} 计数器，
    配合 docker/prometheus-alert-rules.yml 规则触发告警
  - 冷却：同一 code 在 ALERT_WEBHOOK_COOLDOWN 秒内只推一次，防止告警风暴
"""

import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Literal

from backend.shared.logger import logger


@dataclass
class PlanAlert:
    """计划层面的告警事件"""
    timestamp: str
    level: Literal["info", "warn", "error"]
    code: str
    message: str
    detail: dict


ALERT_CODES: dict[str, tuple[str, str]] = {
    "PLAN_EMPTY":             ("warn",  "Planner 返回空计划，降级为 RAG 兜底"),
    "PLAN_JSON_INVALID":      ("warn",  "Planner 输出无法解析为 JSON，使用兜底"),
    "PLAN_CAP_INVALID":       ("warn",  "计划包含无效 capability，已跳过"),
    "PLAN_MISROUTE":          ("warn",  "Critique 检测到 capability 不匹配，已修正"),
    "CRITIQUE_FAILED":        ("warn",  "Plan Critique 调用失败，使用原计划"),
    "SUPERVISOR_MAX_LOOP":    ("error", "Supervisor 达到最大循环次数，强制终止"),
    "WORKER_TIMEOUT":         ("error", "Worker 执行超时"),
    "WORKER_RETRY_EXHAUST":   ("error", "Worker 重试耗尽，最终失败"),
    "RERANKER_UNAVAILABLE":   ("warn",  "CrossEncoder 不可用，降级为 BM25 过滤"),
    "DEGRADATION_TRIGGER":    ("info",  "触发降级链"),
    # P1-7: LLM 韧性链告警
    "LLM_CIRCUIT_OPEN":       ("error", "LLM 熔断器开路，请求快速失败"),
    "LLM_FALLBACK_USED":      ("warn",  "LLM 故障，已切换备用模型"),
    "LLM_DEGRADED_ANSWER":    ("error", "LLM 主/备均失败，返回降级拒答话术"),
}

_LEVEL_ORDER = {"info": 0, "warn": 1, "error": 2}


def make_alert(code: str, detail: dict | None = None) -> PlanAlert:
    """根据告警代码创建 PlanAlert 实例"""
    tz_utc8 = timezone(timedelta(hours=8))
    level, message = ALERT_CODES.get(code, ("warn", f"未知告警: {code}"))
    return PlanAlert(
        timestamp=datetime.now(tz_utc8).isoformat(timespec="seconds"),
        level=level, code=code, message=message, detail=detail or {},
    )


DEGRADATION_LOG_DIR = "logs"
DEGRADATION_LOG_FILE = os.path.join(DEGRADATION_LOG_DIR, "degradation.jsonl")


# =====================================================
# P1-8: webhook 外推
# =====================================================

# 配置（环境变量，见 .env.example）
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "").strip()
ALERT_WEBHOOK_TYPE = os.getenv("ALERT_WEBHOOK_TYPE", "generic").strip().lower()
# 低于该级别的告警不外推（info < warn < error）
ALERT_MIN_LEVEL = os.getenv("ALERT_MIN_LEVEL", "warn").strip().lower()
# 同一 code 的推送冷却（秒），防告警风暴
ALERT_WEBHOOK_COOLDOWN = float(os.getenv("ALERT_WEBHOOK_COOLDOWN", "300"))

_last_push: dict[str, float] = {}
_last_push_lock = threading.Lock()


def _webhook_payload(alert: PlanAlert) -> dict:
    """按通道类型构造 POST body。"""
    text = (
        f"[{alert.level.upper()}] {alert.message}\n"
        f"code: {alert.code}\n"
        f"time: {alert.timestamp}\n"
        f"detail: {json.dumps(alert.detail, ensure_ascii=False)[:500]}"
    )
    if ALERT_WEBHOOK_TYPE == "wecom":
        # 企业微信群机器人
        return {"msgtype": "text", "text": {"content": text}}
    if ALERT_WEBHOOK_TYPE == "dingtalk":
        # 钉钉群机器人（需在钉钉群配置自定义关键词，如 "告警"）
        return {
            "msgtype": "text",
            "text": {"content": f"告警 {text}"},
        }
    if ALERT_WEBHOOK_TYPE == "feishu":
        # 飞书群机器人
        return {"msg_type": "text", "content": {"text": text}}
    # generic: 完整结构化 JSON（自建接收端 / Alertmanager webhook）
    return asdict(alert)


def _push_webhook(alert: PlanAlert) -> None:
    """推送告警到 webhook（best-effort，任何失败只记日志）。"""
    import time as _time

    if not ALERT_WEBHOOK_URL:
        return
    if _LEVEL_ORDER.get(alert.level, 0) < _LEVEL_ORDER.get(ALERT_MIN_LEVEL, 1):
        return
    # 冷却检查
    now = _time.monotonic()
    with _last_push_lock:
        last = _last_push.get(alert.code, 0.0)
        if now - last < ALERT_WEBHOOK_COOLDOWN:
            return
        _last_push[alert.code] = now

    payload = _webhook_payload(alert)

    def _post():
        try:
            import requests
            resp = requests.post(
                ALERT_WEBHOOK_URL, json=payload, timeout=5,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 300:
                logger.warning(
                    f"[Alerts] webhook 推送返回 {resp.status_code}: {resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"[Alerts] webhook 推送失败: {e}")

    # fire-and-forget：告警链路绝不能阻塞业务线程
    threading.Thread(target=_post, name="alert-webhook", daemon=True).start()


def log_degradation(alert: PlanAlert) -> None:
    """记录降级事件：JSONL 文件 + Prometheus 计数 + webhook 外推（均非阻塞）。"""
    # 1) Prometheus 计数（失败不影响其余通道）
    try:
        from backend.observability.metrics import degradation_alerts_total
        degradation_alerts_total.labels(code=alert.code, level=alert.level).inc()
    except Exception:
        pass
    # 2) 本地 JSONL
    try:
        os.makedirs(DEGRADATION_LOG_DIR, exist_ok=True)
        with open(DEGRADATION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(alert), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"[Alerts] 降级日志写入失败: {e}")
    # 3) webhook 外推（P1-8）
    try:
        _push_webhook(alert)
    except Exception as e:
        logger.warning(f"[Alerts] webhook 外推异常: {e}")
