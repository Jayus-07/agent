"""context_budget.collapse — L4 Context Collapse（零 LLM、可回滚、确定性）

职责：当 active context 用量达到 CONTEXT_L4_TRIGGER_RATIO 时，把较早的普通
user/assistant 历史折叠为「固定 policy SystemMessage + <historical_context>
数据 AIMessage」，回收 token。

硬约束（规格 §十二~§二十；角色安全见 role_safety.py，2026-09-23 P0-2）：
  - 零 LLM API 调用；Projection 是模板文本，不是摘要
  - 非破坏性：只影响 active context，原始 chat_messages 不动
  - 可恢复：FoldRegistry.restore_fold(fold_id) 后，后续 projection 重新
    包含对应范围（原始消息从未删除，无需"恢复数据"）
  - 永不折叠：SystemMessage / 最近 CONTEXT_L4_KEEP_RECENT_TURNS 轮 /
    当前用户消息；域图状态（Confirmation/Handoff/Travel/Selection）不在
    消息层，天然不受影响
  - 用户历史内容不得进入 SystemMessage：数据只出现在 AIMessage 的
    <historical_context> 标签内
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.shared.logger import logger


def _cfg(name: str) -> Any:
    import backend.config as config
    return getattr(config, name)


def _is_system(msg: Any) -> bool:
    return type(msg).__name__ == "SystemMessage"


def _count_tokens(text: str) -> int:
    from backend.memory.token_budget import count_tokens
    return count_tokens(text)


@dataclass
class ContextFold:
    """描述一次折叠的范围与收益（不含被折叠内容本身）。"""

    fold_id: str
    from_index: int          # 被折叠消息在原列表中的起始下标
    to_index: int            # 结束下标（含）
    message_count: int
    original_tokens: int
    projected_tokens: int
    from_message_id: str | None = None
    to_message_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    reversible: bool = True

    def to_dict(self) -> dict:
        return {
            "fold_id": self.fold_id,
            "from_index": self.from_index,
            "to_index": self.to_index,
            "message_count": self.message_count,
            "original_tokens": self.original_tokens,
            "projected_tokens": self.projected_tokens,
            "from_message_id": self.from_message_id,
            "to_message_id": self.to_message_id,
            "created_at": self.created_at.isoformat(),
            "reversible": self.reversible,
        }


def build_projection_text(fold: ContextFold) -> str:
    """确定性 Projection 模板（不调用模型，不生成"摘要"）。"""
    return (
        "[Earlier conversation folded]\n\n"
        f"{fold.message_count} earlier messages were omitted from the active "
        "prompt to stay within the context budget.\n\n"
        f"Message range:\n{fold.from_index + 1} - {fold.to_index + 1}\n\n"
        "The original messages remain available in session history."
    )


def fold_messages(
    messages: list,
    *,
    keep_recent_turns: int | None = None,
) -> tuple[list, ContextFold | None]:
    """把较旧的普通历史折叠为「固定 policy SystemMessage + 历史数据 AIMessage」。

    返回 (折叠后的消息列表, ContextFold | None)。无可折叠内容时原样返回。
    折叠对象：除 SystemMessage 外、最近 keep_recent_turns 轮之前的普通消息
    （一组 user+assistant 记一轮；尾部孤立的 user 消息算最后一轮的一部分，
    即当前问题，永不折叠）。

    角色安全（P0-2）：被折叠的用户历史**不进入任何 SystemMessage**——
    数据块放在 AIMessage 的 <historical_context> 标签内（untrusted data），
    SystemMessage 只承载进程内固定的 policy 声明文本。
    """
    if not messages:
        return messages, None
    if keep_recent_turns is None:
        keep_recent_turns = int(_cfg("CONTEXT_L4_KEEP_RECENT_TURNS"))

    # 定位可折叠窗口：[最后一条 SystemMessage 之后的普通消息] 里
    # 留下最近 keep_recent_turns 轮，其余为折叠候选
    non_system_idx = [i for i, m in enumerate(messages) if not _is_system(m)]
    if len(non_system_idx) < 2:
        return messages, None

    # 从尾部往前数 user 消息确定轮边界（user 消息开启一轮）；
    # 恰好保留最近 keep_recent_turns 轮，更早的进入折叠窗口
    user_positions = [i for i in non_system_idx
                      if type(messages[i]).__name__ == "HumanMessage"]
    if len(user_positions) <= keep_recent_turns:
        return messages, None  # 轮数不足阈值，无可折叠

    boundary = user_positions[-keep_recent_turns]  # 最早保留轮的起点
    fold_start = non_system_idx[0]
    fold_end = boundary - 1
    if fold_end < fold_start:
        return messages, None

    candidates = messages[fold_start:fold_end + 1]
    original_tokens = sum(
        _count_token_message(m) for m in candidates)

    fold = ContextFold(
        fold_id=f"fold-{uuid.uuid4().hex[:12]}",
        from_index=fold_start,
        to_index=fold_end,
        message_count=len(candidates),
        original_tokens=original_tokens,
        projected_tokens=0,  # 下面算
        from_message_id=str(getattr(candidates[0], "id", "") or "") or None,
        to_message_id=str(getattr(candidates[-1], "id", "") or "") or None,
    )
    from backend.context_budget.role_safety import build_historical_context
    projection_msgs = build_historical_context(
        build_projection_text(fold),
        meta={
            "kind": "l4_fold",
            "fold_id": fold.fold_id,
            "source_range": [fold.from_index, fold.to_index],
            "message_count": fold.message_count,
            "from_message_id": fold.from_message_id,
            "to_message_id": fold.to_message_id,
            "created_at": fold.created_at.isoformat(),
            "reversible": True,
        })
    fold.projected_tokens = sum(
        _count_token_message(m) for m in projection_msgs)
    if fold.projected_tokens >= original_tokens:
        # 折叠无收益（候选太少太小），不折
        return messages, None

    folded = (
        list(messages[:fold_start])
        + projection_msgs
        + list(messages[fold_end + 1:])
    )
    return folded, fold


def _count_token_message(msg: Any) -> int:
    from backend.memory.token_budget import count_message_tokens
    return count_message_tokens(msg)


def SystemMessage_from_text(text: str):
    from langchain_core.messages import SystemMessage
    return SystemMessage(content=text)


class FoldRegistry:
    """本轮请求内的 fold 台账：记录 + 恢复（不持久化完整快照）。

    "恢复"语义：restore 后对应范围不再被折叠，后续 projection 重新包含
    原始消息（原始内容一直在 chat_messages / 本轮输入里）。
    """

    def __init__(self) -> None:
        self._folds: dict[str, ContextFold] = {}
        self._restored: set[str] = set()

    def register(self, fold: ContextFold) -> None:
        self._folds[fold.fold_id] = fold

    def get(self, fold_id: str) -> ContextFold | None:
        return self._folds.get(fold_id)

    def restore_fold(self, fold_id: str) -> bool:
        """恢复一个 fold：后续 projection 重新包含对应消息范围。"""
        if fold_id not in self._folds:
            return False
        self._restored.add(fold_id)
        logger.info(f"[ContextFold] restore_fold={fold_id}")
        return True

    @property
    def active_folds(self) -> list[ContextFold]:
        return [f for fid, f in self._folds.items() if fid not in self._restored]

    def to_state(self) -> list[dict]:
        """序列化进图状态（checkpointer 兼容：纯字段 dict，无快照内容）。"""
        return [f.to_dict() for f in self._folds.values()]
