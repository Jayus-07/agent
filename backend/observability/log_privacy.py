"""log_privacy.py — 日志隐私辅助（Platform Readiness STOP C14）。

路由层 INFO 日志此前记录 40-60 字 query 预览。生产口径统一为：
**超短预览（≤16 字）+ 原文长度**——保留排障归因能力（命中了哪类请求、
多长），不把用户输入主体内容写进日志。密钥/凭据另有硬门（E15），此处
只管 query 文本。
"""
from __future__ import annotations

_PREVIEW_LIMIT = 16


def query_preview(query: str, limit: int = _PREVIEW_LIMIT) -> str:
    """截断预览 + 原文长度（如 `12字:帮我规划厦门的行程`）；空值安全。"""
    q = (query or "").strip()
    if not q:
        return "0字:<empty>"
    if len(q) <= limit:
        return f"{len(q)}字:{q}"
    return f"{len(q)}字:{q[:limit]}…"
