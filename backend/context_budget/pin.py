"""context_budget.pin — Semantic Pin 上下文保留策略（2026-09-23 P1-2）

裁剪不再依赖「最后一条 = 当前问题」的纯位置假设：Agent/tool calling 中
消息尾部可能是 ToolMessage / 带 tool_calls 的 assistant 回复。

保留优先级（永不丢弃，与 token 预算无关）：
  1. SystemMessage（平台规则 / 固定 policy）——自动 pin
  2. 当前用户消息（**最后一条 HumanMessage**，不是 messages[-1]）——自动 pin
  3. 活跃 tool call 对（最近一组 assistant(tool_calls)+ToolMessage）——自动 pin
  4. 显式业务 pin：active confirmation / 业务实体 / 必需执行结果——由调用方
     经 PinnedContext 标注（消息 id 或下标；域图状态层知道什么是确认态，
     pin 模块不做内容猜测，保持确定性）

原子性（规格 §十一）：assistant(tool_calls) 与其后续 ToolMessage 视为
一个 atomic group——要留一起留，要删一起删，绝不允许拆对（否则
OpenAI-compatible provider 报 invalid tool call sequence / orphan tool message）。
"""

from __future__ import annotations

from typing import Any

# 显式 pin 种类（观测/日志用）
PIN_SYSTEM = "system_policy"
PIN_CURRENT_QUERY = "current_user_message"
PIN_ACTIVE_TOOL_PAIR = "active_tool_pair"
PIN_CONFIRMATION = "active_confirmation"
PIN_ENTITY = "business_entity"
PIN_EXECUTION_RESULT = "required_execution_result"


def _is_system(msg: Any) -> bool:
    return type(msg).__name__ == "SystemMessage"


def _is_human(msg: Any) -> bool:
    return type(msg).__name__ == "HumanMessage"


def _has_tool_calls(msg: Any) -> bool:
    return bool(getattr(msg, "tool_calls", None))


def _is_tool(msg: Any) -> bool:
    return type(msg).__name__ == "ToolMessage"


def build_atomic_groups(messages: list) -> list[list[int]]:
    """把消息列表切成原子组（每组是一个不可拆分的保留/丢弃单元）。

    - SystemMessage：单条一组
    - assistant(tool_calls) + 其后**紧邻**的全部 ToolMessage：一组
    - 其余消息：单条一组
    组内保持原相对顺序；返回下标组的列表。
    """
    groups: list[list[int]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if _has_tool_calls(msg):
            group = [i]
            j = i + 1
            while j < n and _is_tool(messages[j]):
                group.append(j)
                j += 1
            groups.append(group)
            i = j
            continue
        groups.append([i])
        i += 1
    return groups


def collect_pin_indices(messages: list,
                        pinned_message_ids: set[str] | None = None) -> set[int]:
    """计算语义 pin 的消息下标集合（结构性自动 pin + 显式 id pin）。"""
    pins: set[int] = set()
    current_query_idx: int | None = None
    active_pair: list[int] = []

    for i, msg in enumerate(messages):
        if _is_system(msg):
            pins.add(i)
        elif _is_human(msg):
            current_query_idx = i  # 不断后移 → 最终即最后一条用户消息
        if _has_tool_calls(msg):
            active_pair = [i]
        elif _is_tool(msg) and active_pair:
            active_pair.append(i)
        else:
            active_pair = []

    if current_query_idx is not None:
        pins.add(current_query_idx)
    # 活跃 tool 对：只 pin 最近一组；更早的对只享受原子性，不享受 pin
    pins.update(active_pair)

    if pinned_message_ids:
        wanted = {str(mid) for mid in pinned_message_ids}
        for i, msg in enumerate(messages):
            if str(getattr(msg, "id", "") or "") in wanted:
                pins.add(i)
    return pins


class PinnedContext:
    """调用方向裁剪器登记业务级 pin 的载体（confirmation / 实体 / 必需结果）。

    用法（域图节点 / 编排层持有状态，知道什么不能丢）：
        pins = PinnedContext()
        pins.mark_index(12, PIN_CONFIRMATION)          # 按下标
        pins.mark_message_id("msg_abc", PIN_ENTITY)    # 按消息 id
        prepared = manager.prepare_llm_context(messages=..., pins=pins)
    """

    def __init__(self) -> None:
        self._indices: dict[int, str] = {}
        self._message_ids: dict[str, str] = {}

    def mark_index(self, index: int, kind: str) -> "PinnedContext":
        self._indices[int(index)] = kind
        return self

    def mark_message_id(self, message_id: str, kind: str) -> "PinnedContext":
        self._message_ids[str(message_id)] = kind
        return self

    def resolve(self, messages: list) -> set[int]:
        pins = collect_pin_indices(
            messages, set(self._message_ids) or None)
        for idx in self._indices:
            if 0 <= idx < len(messages):
                pins.add(idx)
        return pins
