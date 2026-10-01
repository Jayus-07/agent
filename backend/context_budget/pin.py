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

import contextvars
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


def _is_projection_pair_start(messages: list, i: int) -> bool:
    """投影二元组起点：固定 policy System + 紧随的历史数据 AIMessage。"""
    if i + 1 >= len(messages):
        return False
    from backend.context_budget.role_safety import (
        is_historical_data_message,
        is_policy_system_message,
    )
    return (is_policy_system_message(messages[i])
            and is_historical_data_message(messages[i + 1]))


def build_atomic_groups(messages: list) -> list[list[int]]:
    """把消息列表切成原子组（每组是一个不可拆分的保留/丢弃单元）。

    - SystemMessage：单条一组
    - assistant(tool_calls) + 其后**紧邻**的全部 ToolMessage：一组
    - 投影二元组（2026-10-01 STOP C）：固定 policy SystemMessage + 紧随的
      <historical_context> 数据 AIMessage 一组——L2 裁剪只按组保留/丢弃，
      拆开会出现「只剩 policy 空壳」或「数据块失去 policy 声明」
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
        if _is_projection_pair_start(messages, i):
            groups.append([i, i + 1])
            i += 2
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
        pins.mark_content_match("20260922001", PIN_ENTITY)  # 按内容锚定
        prepared = manager.prepare_llm_context(messages=..., pins=pins)
    """

    # 内容锚定值的最短长度：业务实体 id（订单号/退款单号等）≥4 字符；
    # 更短的值（如「1」）会命中过多消息，等于变相全量 pin，直接拒绝
    _MIN_CONTENT_MATCH_LEN = 4
    # 单个锚定值最多 pin 的消息条数（取最近的 K 条，防御异常值全量命中）
    _MAX_MATCHES_PER_VALUE = 3

    def __init__(self) -> None:
        self._indices: dict[int, str] = {}
        self._message_ids: dict[str, str] = {}
        self._content: list[tuple[str, str]] = []

    def mark_index(self, index: int, kind: str) -> "PinnedContext":
        self._indices[int(index)] = kind
        return self

    def mark_message_id(self, message_id: str, kind: str) -> "PinnedContext":
        self._message_ids[str(message_id)] = kind
        return self

    def mark_content_match(self, value: str, kind: str) -> "PinnedContext":
        """内容锚定：pin 所有包含该确定性值的消息（解析在 resolve 时进行）。

        生产 active context 的消息不带稳定 id（memory/session.py 只按
        content 构造），按下标标注对索引移位脆弱——按业务实体值锚定，
        裁剪时现解析，零猜测、确定性。短值/空值直接忽略（不猜）。
        """
        value = str(value or "").strip()
        if len(value) >= self._MIN_CONTENT_MATCH_LEN:
            self._content.append((value, kind))
        return self

    def resolve(self, messages: list) -> set[int]:
        pins = collect_pin_indices(
            messages, set(self._message_ids) or None)
        for idx in self._indices:
            if 0 <= idx < len(messages):
                pins.add(idx)
        if self._content:
            pins.update(self._resolve_content(messages))
        return pins

    def _resolve_content(self, messages: list) -> list[int]:
        """内容锚定解析：逐值倒序扫描，最多 pin 最近 K 条命中（确定性）。"""
        matched: list[int] = []
        for value, _kind in self._content:
            hits: list[int] = []
            for i in range(len(messages) - 1, -1, -1):
                if value in str(getattr(messages[i], "content", "") or ""):
                    hits.append(i)
                    if len(hits) >= self._MAX_MATCHES_PER_VALUE:
                        break
            matched.extend(hits)
        return matched


# ── 请求级业务 pin 注册口（2026-09-23 生产收口 B4）────────────────────
# 域图入口（如 cs_state_loader）持有快照态，知道「当前请求正确执行必须
# 保留什么」；LLM 调用点在专家/节点深处，逐层传 pins 不现实——用
# ContextVar 作请求级通道：同请求内（含 supervisor Send 子任务，任务
# 创建时继承上下文拷贝）可见，请求结束随上下文销毁 = 天然 supersede/
# expiry（order A → order B 由下一轮状态重新注册替换，永不跨轮累积）。
_REQUEST_PINS: "contextvars.ContextVar[tuple[tuple[str, str], ...] | None]" = \
    contextvars.ContextVar("cb_request_pins", default=None)


def register_request_pin(value: str, kind: str) -> None:
    """登记当前请求的内容锚定 pin（幂等追加；短值按 PinnedContext 规则忽略）。"""
    value = str(value or "").strip()
    if len(value) < PinnedContext._MIN_CONTENT_MATCH_LEN:
        return
    current = _REQUEST_PINS.get() or ()
    if (value, kind) in current:
        return
    _REQUEST_PINS.set(current + ((value, kind),))


def request_pin_values() -> tuple[tuple[str, str], ...]:
    """读取当前请求已登记的 (锚定值, kind) 列表（无则空元组）。"""
    return _REQUEST_PINS.get() or ()


def pins_from_request() -> "PinnedContext | None":
    """把请求级注册转成 PinnedContext；未注册任何 pin 时返回 None（零开销）。"""
    values = request_pin_values()
    if not values:
        return None
    pins = PinnedContext()
    for value, kind in values:
        pins.mark_content_match(value, kind)
    return pins
