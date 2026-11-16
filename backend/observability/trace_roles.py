"""Trace Span 的模型角色识别。"""


_SPECIALIZED_MODEL_ROLES = frozenset({"embedding", "rerank"})


def span_model_role(span: object) -> str:
    """读取 Span 上的模型角色，兼容字典与对象形态。"""
    getter = (lambda key, default=None: span.get(key, default)) if isinstance(span, dict) \
        else (lambda key, default=None: getattr(span, key, default))
    metrics = getter("metrics", {}) or {}
    attributes = getter("attributes", {}) or {}
    role = metrics.get("role") or attributes.get("role")
    return str(role or "").strip().lower()


def is_specialized_model_span(span: object) -> bool:
    """判断 Span 是否属于 embedding / rerank 等专项模型调用。"""
    return span_model_role(span) in _SPECIALIZED_MODEL_ROLES


def is_llm_generation_span(span: object) -> bool:
    """判断 Span 是否代表聊天模型生成。

    旧 Trace 没有 role 时继续按生成调用兼容；新 Trace 按模型角色区分。
    """
    return not is_specialized_model_span(span)
