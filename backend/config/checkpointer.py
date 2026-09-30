"""checkpointer.py — 检查点后端「降级还是硬失败」的统一裁决（结构病审查 P2-10）。

病史
----
主图（`orchestration/graph/checkpointer.py`）与客服域（`customer_service/graph_builder.py`）
在 PostgresSaver 初始化失败时，历史上都是「一条 logger.warning + 静默降级 MemorySaver」，
MemorySaver 再失败就返回 None（等于没有 checkpointer）。本地开发这没问题；生产环境
则意味着：跨轮状态不持久化、interrupt/resume 失效、多副本各存一份 —— 日志里却只有
一行会被刷过去的 warning。与「读失败伪装成正常值」同源：坏消息被就地吞掉。

判据（与 `config.ENVIRONMENT` 同一口径）
----------------------------------------
    ENVIRONMENT=production 且未显式放行  → 抛 CheckpointerUnavailable（fail-loud）
    其余环境 / 显式放行                  → 返回告警文案，调用方继续走 MemorySaver 兜底

显式放行 `CHECKPOINTER_ALLOW_DEGRADE=true`：给「明确接受内存检查点」的部署留的出口
（单副本、演示环境）。生产要降级必须**显式写这个开关**，不允许因为「依赖没装」这种
意外原因悄悄降级。

启动期还有一道对应的拦截（`config/startup.py` 的 production fatal）：开关开着但
psycopg v3 / langgraph-checkpoint-postgres 缺失时直接拒绝启动 —— 那道拦在「第一次
用到之前」，本模块拦在「真要降级的那一刻」（例如 PG 连不上、setup 建表失败）。

不在本模块范围
--------------
旅游域（`travel/graph_builder.py`）不走这里：它把降级作为 `PERSISTENCE_DEGRADED`
状态**上浮到 trace 与行程单**，全链路可见，不是静默吞掉，因此只降级、不硬失败。
"""
from __future__ import annotations

import os

from backend.config import ENVIRONMENT
from backend.shared.logger import logger


def degrade_allowed() -> bool:
    """是否显式放行降级为内存检查点（默认否）。

    每次调用现读 env：单测用 monkeypatch.setenv 即可，运维改 env 后也无需重启进程
    才生效（判据本身仍走 `ENVIRONMENT` 快照）。
    """
    return os.getenv("CHECKPOINTER_ALLOW_DEGRADE", "false").strip().lower() in (
        "1", "true", "yes",
    )


class CheckpointerUnavailable(RuntimeError):
    """生产环境检查点后端不可用且未放行降级 —— 拒绝静默续跑。"""


def degrade_or_raise(owner: str, reason: str, *, exc_info: bool = False) -> str:
    """后端不可用时裁决：返回降级告警文案，或（生产）直接抛异常。

    Args:
        owner: 归属标识，用于日志与报错定位，如 "MainGraph" / "CS Graph"。
        reason: 为什么不可用（缺依赖 / 连不上 / setup 失败），写给人看。
        exc_info: 是否把当前异常栈记入日志（在 except 块内调用时传 True）。

    Returns:
        允许降级时返回告警文案；调用方据此继续走 MemorySaver 兜底。

    Raises:
        CheckpointerUnavailable: ENVIRONMENT=production 且未放行降级。
    """
    consequence = (
        "跨轮状态不持久化：进程重启即失、多副本各存一份、interrupt/resume 失效，"
        "而日志里的「enabled」会让人误以为已持久化"
    )

    # 延迟读模块属性（而非 import 时绑定）：单测 monkeypatch
    # backend.config.checkpointer.ENVIRONMENT 即可覆盖，不用改真实 env。
    if ENVIRONMENT == "production" and not degrade_allowed():
        raise CheckpointerUnavailable(
            f"[{owner}] checkpointer 后端不可用：{reason}。"
            f"ENVIRONMENT=production 下拒绝静默降级（后果：{consequence}）。"
            "处理：补齐 psycopg v3 / langgraph-checkpoint-postgres 或修好后端；"
            "若确实接受内存检查点，显式设置 CHECKPOINTER_ALLOW_DEGRADE=true"
            "（该决定应写进部署说明）；或关掉对应的 *_CHECKPOINTER_ENABLED 开关"
            "（明确不使用，而不是假装在用）。"
        )

    msg = (
        f"[{owner}] checkpointer 后端不可用（{reason}），已降级为 MemorySaver："
        f"{consequence}。"
    )
    logger.warning(msg, exc_info=exc_info)
    return msg


__all__ = [
    "CheckpointerUnavailable",
    "degrade_allowed",
    "degrade_or_raise",
]
