"""llm_router.py — LLM Router（最后兜底，2026-08-11）

只在 Rule + Embedding 都没高置信度时调用。

用 qwen2.5:3b 本地（用户已有）：
- 1 次 LLM 调用 ~3-5s
- Prompt 极简（< 200 token 输入）
- 输出 JSON（capability 列表 + reason）
"""
from __future__ import annotations

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
    ALL_CAPABILITIES,
    WORKFLOW_NAMES,
)


DEFAULT_ROUTER_PROMPT = """路由能力选择，输出JSON。
能力: sql.query|rag.search|business.analyze|report.generate|email.send|data.export|web.search|data.collect|daily_report|inventory_alert
问题: {query}
输出: {{"execution_mode":"direct|plan|workflow","candidates":[{{"name":"能力","score":0-1}}],"reason":"一句话"}}"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON（P1-14：统一到 shared/json_extractor）。

    宽松语义：全失败返回 None（调用方走 _fallback）。
    """
    from backend.shared.json_extractor import extract_json

    return extract_json(text)


class LLMRouter:
    """LLM Router：用 qwen2.5:3b 做最后兜底。"""

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    def route(self, query: str) -> RouteDecision:
        """LLM 判断意图 + 选能力。返回 RouteDecision（candidates 来自 LLM）。"""
        from backend.infra.timeout import safe_call_with_timeout
        from backend.infra.llm import llm
        from backend.shared.logger import logger

        try:
            from backend.prompts.service import prompt_service
            r = prompt_service.render_sync("router.llm", query=query[:200])
            prompt = r.text
        except Exception:
            prompt = DEFAULT_ROUTER_PROMPT.format(query=query[:200])

        try:
            raw = safe_call_with_timeout(
                llm.invoke,
                timeout=self.timeout,
                default_value=None,
                error_message=f"[LLMRouter] 推理超时 ({self.timeout}s)",
                input=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.warning(f"[LLMRouter] 推理异常: {e}")
            return self._fallback(query, reason=str(e))

        if raw is None:
            return self._fallback(query, reason="timeout")

        content = raw.content if hasattr(raw, "content") else str(raw)
        parsed = _extract_json(content)

        if not parsed or "candidates" not in parsed:
            return self._fallback(query, reason="parse_failed", raw=content[:200])

        # 解析
        try:
            mode_str = parsed.get("execution_mode", "plan")
            execution_mode = ExecutionMode(mode_str) if mode_str in ("direct", "plan", "workflow") else ExecutionMode.PLAN
        except Exception:
            execution_mode = ExecutionMode.PLAN

        candidates = []
        for c in parsed.get("candidates", []):
            if isinstance(c, dict) and "name" in c:
                # 校验 name 在 ALL_CAPABILITIES 或 workflow 中
                name = c["name"]
                if name in ALL_CAPABILITIES or name in WORKFLOW_NAMES:
                    candidates.append(
                        CapabilityScore(name=name, score=float(c.get("score", 0.5)))
                    )

        if not candidates:
            return self._fallback(query, reason="no_valid_candidates")

        return RouteDecision(
            execution_mode=execution_mode,
            candidates=candidates,
            confidence=0.7,  # LLM 路由给中等置信度
            reason=parsed.get("reason", ""),
            workflow_name=parsed.get("workflow_name"),
        )

    def _fallback(self, query: str, reason: str, raw: str = "") -> RouteDecision:
        """LLM 失败时的 fallback：plan mode + rag.search 默认。"""
        from backend.shared.logger import logger
        logger.warning(f"[LLMRouter] 失败 fallback: {reason}")
        return RouteDecision(
            execution_mode=ExecutionMode.PLAN,
            candidates=[CapabilityScore(name="rag.search", score=0.5)],
            confidence=0.3,
            reason=f"LLM Router 失败 ({reason})，默认 RAG",
        )
