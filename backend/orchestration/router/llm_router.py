"""llm_router.py — LLM Router（最后兜底，2026-08-11）

只在 Rule + Embedding 都没高置信度时调用。

用 qwen2.5:3b 本地（用户已有）：
- 1 次 LLM 调用 ~3-5s
- Prompt 极简（< 200 token 输入）
- 输出 JSON（capability 列表 + reason）
"""
from __future__ import annotations

import json
import re
from typing import List

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
    ALL_CAPABILITIES,
    WORKFLOW_NAMES,
)


LLM_ROUTER_PROMPT = """你是企业 Agent 路由。

【可用能力】
- sql.query: 业务数据查询
- rag.search: 企业知识库查询（制度/流程/SOP）
- business.analyze: 业务分析（找原因/给建议）
- report.generate: 报告生成
- email.send: 邮件发送
- data.export: 数据导出
- web.search / web.crawl: 联网搜索/抓取
- data.collect: 数据采集
- daily_report / inventory_alert: 已注册工作流

【用户问题】
{query}

判断需要哪些能力（按重要性排序），输出 JSON（只输出 JSON）:
{{
  "execution_mode": "direct" | "plan" | "workflow",
  "candidates": [{{"name": "能力", "score": 0-1}}, ...],
  "reason": "一句话判断依据",
  "workflow_name": "（如 workflow 模式）"
}}
"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


class LLMRouter:
    """LLM Router：用 qwen2.5:3b 做最后兜底。"""

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def route(self, query: str) -> RouteDecision:
        """LLM 判断意图 + 选能力。返回 RouteDecision（candidates 来自 LLM）。"""
        from backend.infra.timeout import safe_call_with_timeout
        from backend.infra.llm import llm
        from backend.shared.logger import logger

        prompt = LLM_ROUTER_PROMPT.format(query=query[:500])

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
