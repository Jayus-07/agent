"""observability/trace_source.py — trace 来源三分类（2026-10-08 #12）

用户口径「先只搞三个」：旅游域 / 客服 / AI 助手。分类依据是 trace 自身
已有的事实（13 字段 runtime 归因写入的 ``tags.runtime_domain``、客服域
写的 ``tags.domain``、主图 ``workflow_name=agent``），不新增任何埋点。

单一事实源：后端（列表过滤 / stats 聚合 / DTO source 字段）与前端展示
全部消费 ``classify_trace_source`` 的结果，禁止在别处第二套映射（G2）。

边界说明：
  - ``selection_funnel``（选品漏斗）是独立域图但不在本口径三分类内，
    归 SOURCE_UNKNOWN；后续扩展第四来源时在这里加一行即可。
  - ``rag_agent``（独立 RAG 链）与 knowledge_index 等非主图工作流同样
    归 SOURCE_UNKNOWN——它们不是「AI 助手对话」。
"""
from __future__ import annotations

SOURCE_TRAVEL = "travel"
SOURCE_CS = "cs"
SOURCE_AI_ASSISTANT = "ai_assistant"
SOURCE_UNKNOWN = ""

# 已知域图字面量中「不属于三分类」的部分：这些 domain 值即使出现在主图
# workflow 上也不能冒充 AI 助手。
_NON_AI_DOMAINS = {"travel", "customer_service", "selection_funnel"}

SOURCE_LABELS: dict[str, str] = {
    SOURCE_TRAVEL: "旅游域",
    SOURCE_CS: "客服",
    SOURCE_AI_ASSISTANT: "AI 助手",
    SOURCE_UNKNOWN: "其他",
}


def classify_trace_source(workflow_name: str | None, tags: dict | str | None) -> str:
    """trace → 三来源之一（纯函数；未命中三分类返回空串）。

    tags 兼容两种形态：dict（collector 详情 / DTO）与 JSON 文本
    （analytics trace_summary 行），解析失败按无标签处理。
    """
    if isinstance(tags, str):
        import json

        try:
            tags = json.loads(tags)
        except (TypeError, ValueError):
            tags = {}
    tags = tags if isinstance(tags, dict) else {}
    domain = str(tags.get("runtime_domain") or tags.get("domain") or "").strip().lower()
    if domain == "travel":
        return SOURCE_TRAVEL
    if domain == "customer_service":
        return SOURCE_CS
    if str(workflow_name or "").strip() == "agent" and domain not in _NON_AI_DOMAINS:
        return SOURCE_AI_ASSISTANT
    return SOURCE_UNKNOWN


__all__ = [
    "SOURCE_TRAVEL",
    "SOURCE_CS",
    "SOURCE_AI_ASSISTANT",
    "SOURCE_UNKNOWN",
    "SOURCE_LABELS",
    "classify_trace_source",
]
