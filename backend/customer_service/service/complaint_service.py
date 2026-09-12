"""customer_service/service/complaint_service.py — 投诉处理服务

投诉检测 → 严重度评估 → 创建工单 → 安抚响应 → 触发转接。

Phase 5: 模拟工单写入 (simulate_execute)。
Phase 6: 替换为 DB 持久化。

设计参考: docs/customer-service/design.md §11
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.config.customer_service import COMPLAINT_PATTERNS
from backend.shared.logger import logger


@dataclass
class ComplaintDetection:
    is_complaint: bool
    severity: str  # "low" / "medium" / "high"
    matched_patterns: list[str] = field(default_factory=list)


@dataclass
class ComplaintTicket:
    ticket_id: str
    user_id: str
    conversation_id: str
    severity: str
    summary: str
    status: str = "open"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_EMPATHY_RESPONSES = {
    "high": (
        "非常抱歉给您带来了不好的体验，我们非常重视您反馈的问题。"
        "我已为您创建了投诉工单，将尽快安排专人跟进处理。"
        "如有紧急事项，您也可以随时联系我们。"
    ),
    "medium": (
        "很抱歉给您带来了不便，我们已记录您的反馈。"
        "会尽快安排相关人员跟进处理，请您耐心等待。"
    ),
    "low": (
        "感谢您的反馈，我们已记录您的意见。"
        "会持续改进服务质量，给您带来不便敬请谅解。"
    ),
}


class ComplaintService:

    def detect(self, query: str) -> ComplaintDetection:
        """检测文本是否包含投诉内容并评估严重度。

        严重度规则:
          - 0 个 pattern 命中 → is_complaint=False, severity="low"
          - 1 个 pattern 命中 → is_complaint=True, severity="medium"
          - >= 2 个 pattern 命中 → is_complaint=True, severity="high"
        """
        if not query or not query.strip():
            return ComplaintDetection(is_complaint=False, severity="low")

        matched: list[str] = []
        for pattern in COMPLAINT_PATTERNS:
            if pattern.search(query):
                matched.append(pattern.pattern)

        if not matched:
            return ComplaintDetection(is_complaint=False, severity="low")

        severity = "high" if len(matched) >= 2 else "medium"
        return ComplaintDetection(
            is_complaint=True,
            severity=severity,
            matched_patterns=matched,
        )

    def detect_with_llm_fallback(self, query: str) -> ComplaintDetection:
        """规则优先的级联检测：规则命中直接用，0 命中时 LLM 兜底评估。

        调用方（complaint expert）仅在路由已判定投诉意图后进入，此时规则
        0 命中意味着委婉表达（关键词盲区，如"再不处理就没法用了"），
        用 LLM 评估严重度；LLM 失败或判非投诉则回退规则结果。
        """
        detection = self.detect(query)
        if detection.is_complaint:
            return detection

        from backend.config.customer_service import CS_COMPLAINT_LLM_ENABLED
        if not CS_COMPLAINT_LLM_ENABLED:
            return detection

        verdict = self._llm_assess(query)
        if verdict is None or not verdict.get("is_complaint"):
            return detection
        return ComplaintDetection(
            is_complaint=True,
            severity=verdict["severity"],
            matched_patterns=["llm_fallback"],
        )

    def _llm_assess(self, query: str) -> dict | None:
        """LLM 评估投诉与严重度。异常或格式无效返回 None（确定性降级）。"""
        try:
            from langchain_core.messages import HumanMessage

            from backend.config.customer_service import CS_COMPLAINT_LLM_TIMEOUT_MS
            from backend.infra.llm import get_llm

            prompt = (
                "你是客服质检员。判断用户消息是否为投诉，并评估严重度。\n"
                f"用户消息: {query[:300]}\n"
                '只回复 JSON: {"is_complaint": true或false, '
                '"severity": "low"或"medium"或"high"}，不要解释。'
            )
            response = get_llm().invoke(
                [HumanMessage(content=prompt)],
                config={"timeout": CS_COMPLAINT_LLM_TIMEOUT_MS / 1000.0},
            )
            content = response.content.strip()
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content)
            if (
                isinstance(data, dict)
                and isinstance(data.get("is_complaint"), bool)
                and data.get("severity") in ("low", "medium", "high")
            ):
                logger.info(
                    "[ComplaintService] LLM 兜底: is_complaint=%s severity=%s",
                    data["is_complaint"], data["severity"],
                )
                return data
            logger.warning("[ComplaintService] LLM 返回格式无效，回退规则结果")
            return None
        except Exception as e:
            logger.warning("[ComplaintService] LLM 兜底失败，回退规则结果: %s", e)
            return None

    def create_ticket(
        self,
        user_id: str,
        conversation_id: str,
        severity: str,
        summary: str,
    ) -> ComplaintTicket:
        """创建投诉工单 (模拟)。"""
        return ComplaintTicket(
            ticket_id=f"COMPLAINT-{uuid.uuid4().hex[:8].upper()}",
            user_id=user_id,
            conversation_id=conversation_id,
            severity=severity,
            summary=summary[:500],
        )

    def build_comfort_response(self, detection: ComplaintDetection, ticket: ComplaintTicket) -> str:
        """构建安抚响应文本。"""
        base = _EMPATHY_RESPONSES.get(detection.severity, _EMPATHY_RESPONSES["low"])
        return f"{base} (工单号: {ticket.ticket_id})"

    def simulate_execute(self, ticket: ComplaintTicket) -> dict:
        """模拟投诉工单写入 (Phase 5)。"""
        return {
            "action": "complaint_ticket_created",
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "severity": ticket.severity,
            "executed": True,
        }


_service_instance: ComplaintService | None = None
_service_lock = threading.Lock()


def get_complaint_service() -> ComplaintService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = ComplaintService()
    return _service_instance
