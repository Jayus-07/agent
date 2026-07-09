"""
alerts.py — 告警与可观测性

PlanAlert 数据类 + 告警代码表 + 降级日志写入。
贯穿 Planner / Critique / Supervisor / Worker / Reporter 全链路。
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Literal
from utils.logger import logger


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
}


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


def log_degradation(alert: PlanAlert) -> None:
    """记录降级事件到 JSONL 文件（非阻塞追加，失败不抛异常）"""
    try:
        os.makedirs(DEGRADATION_LOG_DIR, exist_ok=True)
        with open(DEGRADATION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(alert), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"[Alerts] 降级日志写入失败: {e}")
