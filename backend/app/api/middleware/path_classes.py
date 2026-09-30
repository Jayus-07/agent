"""middleware/path_classes.py — 请求路径分类（共享单一事实源）。

_HIGH_PRIORITY_PREFIXES / _SKIP_PREFIXES 原定义于 concurrency.py，
2026-09-30（主架构改造 P0-1）抽出：分布式准入门（distributed_gate.py）
与进程内优先级门（concurrency.py）必须共享同一份路径分类，
避免两份清单漂移（G2：派生量禁止第二处手抄）。
"""
from __future__ import annotations

# 交互式端点前缀：占满时插队（高优先级），保证对话 P99 TTFT 不被批量任务拖垮
_HIGH_PRIORITY_PREFIXES = (
    "/chat/stream",
    "/chat",
    "/cs",       # 客服对话图
)

# 不阻塞的路径前缀（轻量只读 + 系统端点）
_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/rag/operations",   # 操作审计日志（SQLite 只读）
    "/rag/documents",     # 文档列表/详情（SQLite 只读）
    "/rag/stats",         # 统计（SQLite 只读）
    "/rag/chunks",        # Chunk 详情（SQLite 只读）
    "/rag/search",        # 关键词搜索（只读）
    "/rag/knowledge-bases",  # 知识库列表
    "/rag/health",        # 健康检查
    "/rag/memory",        # 记忆查询
    "/memory",            # 记忆模块（只读查询）
    "/data",              # 数据中心（列表查询）
    "/mcp",               # MCP 服务器列表
    "/reports",           # 报告列表（只读）
    "/schedules",         # 定时任务列表
    "/chat/messages",     # 聊天历史（只读查询）
    "/chat/abort",        # 中止请求（控制信号，需立即处理）
    "/prompts",           # 提示词管理（SQLite/PG 只读查询）
    "/evaluation",        # 评测管理（只读查询 + 历史报告）
)


def is_skip_path(path: str) -> bool:
    """轻量只读/系统端点判定：这些路径不受并发门限制、不消耗槽位。"""
    return path in _SKIP_PREFIXES or any(
        path.startswith(prefix) for prefix in _SKIP_PREFIXES
    )


def priority_of(path: str) -> str:
    """按路径判定优先级：交互式对话 high，其余批量/管理操作 normal。"""
    return "high" if path.startswith(_HIGH_PRIORITY_PREFIXES) else "normal"
