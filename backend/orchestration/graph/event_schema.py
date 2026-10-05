"""orchestration/graph/event_schema.py — SSE 事件契约（P2-1 阶段一：只做校验）

2026-09-30 主架构改造 P2-1。**本阶段 events.py / chat.py 的手写帧构造函数
一律不改**——本文件建立「契约 + 校验门」：后续任何人改动帧构造破坏契约，
``tests/test_sse_event_schema.py`` 立即拦截。阶段二（前端从 schema 生成
TS 类型）另行立项，不在此范围。

契约分三层（与 2026-09-30 实测的 13 种事件一一核对）：
  CORE（六类，帧序约束参与者）：
    meta（首帧握手）/ status / log / delta / done / error（终帧）
  AUX（中段辅助帧，任意位置任意次）：todo / usage / file /
    clarification / context / thinking
  TRANSPORT（传输层保活）：ping——不计入帧序

帧序（AGENTS.md 冻结口径「meta → status/log/delta → done/error」的精确化）：
  ① meta 必为首帧且唯一（HTTP 层注入；runner 层事件流无 meta，
     校验时以 require_meta=False 放行）
  ② done / error 互斥：只能有一个终帧，其后不得再有任何帧
  ③ 中段帧（status/log/delta/AUX）次序与次数不约束

data 契约原则：**只锁源码恒定提供的必填字段**，其余 extra=allow——
契约是回归门不是序列化格式冻结，过紧会对合法新增字段误报。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

CORE_EVENTS: tuple[str, ...] = (
    "meta", "status", "log", "delta", "done", "error",
)
AUX_EVENTS: tuple[str, ...] = (
    "todo", "usage", "file", "clarification", "context", "thinking",
    # 多域隔离收官 M1（2026-10-06）：主图域引导交接卡（目标域+参数包+引导话术），
    # 契约本体 backend/orchestration/contracts/handoff.py::HandoffPayloadV1
    "handoff",
)
TRANSPORT_EVENTS: tuple[str, ...] = ("ping",)
KNOWN_EVENTS: frozenset[str] = frozenset(CORE_EVENTS + AUX_EVENTS + TRANSPORT_EVENTS)
TERMINAL_EVENTS: frozenset[str] = frozenset(("done", "error"))


class _Loose(BaseModel):
    """契约基类：未知字段放行（锁必填，不冻结构）。"""

    model_config = ConfigDict(extra="allow")


# ── CORE 帧契约 ──────────────────────────────────────


class MetaData(_Loose):
    request_id: str
    node_labels: dict


class StatusData(_Loose):
    node: str
    ts: float


class LogData(_Loose):
    node: str
    ts: float


class DeltaData(_Loose):
    content: str
    ts: float


class DoneData(_Loose):
    elapsed: float
    sources: list
    # 2026-10-03 RAG 拒答/置信度语义（缺省 = 正常回答）：answer_status 来自
    # 工具 RAGMETA 标记，取值 rag_no_evidence / rag_permission_denied /
    # rag_hallucination（evidence_gate.models.ANSWER_STATUS_BY_REASON）；
    # confidence = META 自报置信度（0~1），前端低于阈值显示「建议核实」
    answer_status: str | None = None
    confidence: float | None = None
    # 2026-10-05 回复归因稳定码（reply_source）：knowledge_base /
    # data_analysis / realtime_query / system_notice；缺省不标注
    reply_source: str | None = None


class ErrorData(_Loose):
    message: str
    ts: float


# ── AUX / TRANSPORT 帧契约 ──────────────────────────


class TodoData(_Loose):
    items: list
    ts: float


class UsageData(_Loose):
    ts: float


class FileData(_Loose):
    node: str
    files: list
    ts: float


class ClarificationData(_Loose):
    question: str
    options: list
    ts: float


class ContextData(_Loose):
    """context_budget 事件（L2 裁剪/L4 压缩等），字段随事件类型变化，只锁 dict 形态。"""

    model_config = ConfigDict(extra="allow")


class ThinkingData(_Loose):
    content: str
    ts: float


class HandoffData(_Loose):
    """域引导交接卡（contracts/handoff.py::HandoffPayloadV1 的帧面投影）。

    帧门禁只锁源码恒定提供的 v + target_domain；params/text 形态由契约
    模型在构造期严格校验（extra=forbid），此处保持 loose 兼容演进。
    """

    v: int
    target_domain: str


class PingData(_Loose):
    ts: float


class _Frame(BaseModel):
    """帧外壳：event 名 + data 对象。"""

    model_config = ConfigDict(extra="allow")
    event: str
    data: dict


_DATA_MODELS: dict[str, type[BaseModel]] = {
    "meta": MetaData,
    "status": StatusData,
    "log": LogData,
    "delta": DeltaData,
    "done": DoneData,
    "error": ErrorData,
    "todo": TodoData,
    "usage": UsageData,
    "file": FileData,
    "clarification": ClarificationData,
    "context": ContextData,
    "thinking": ThinkingData,
    "handoff": HandoffData,
    "ping": PingData,
}


def validate_frame(frame: dict) -> None:
    """单帧校验：event 必须已知 + data 过对应契约模型。

    Raises:
        ValueError: event 未知。
        pydantic.ValidationError: 帧外壳或 data 契约不符。
    """
    if not isinstance(frame, dict):
        raise ValueError(f"帧必须是 dict，得到 {type(frame).__name__}")
    outer = _Frame.model_validate(frame)
    if outer.event not in KNOWN_EVENTS:
        raise ValueError(
            f"未知事件类型 {outer.event!r}；已知: {sorted(KNOWN_EVENTS)}"
        )
    model = _DATA_MODELS[outer.event]
    model.model_validate(frame.get("data") or {})


def validate_frame_sequence(
    frames: list[dict], *, require_meta: bool = True
) -> list[dict]:
    """帧序校验：逐帧过契约 + 帧序约束。

    Args:
        frames: 完整事件流（dict 列表）。
        require_meta: True（默认，HTTP 层完整流）要求首帧 meta 且唯一；
            False（runner 层事件流，meta 由 chat.py 注入）跳过该约束。

    Returns:
        过滤掉 ping 后的有效帧序列（供调用方进一步断言）。

    Raises:
        ValueError / ValidationError: 违反契约或帧序。
    """
    for frame in frames:
        validate_frame(frame)

    effective = [f for f in frames if f.get("event") != "ping"]

    if require_meta:
        if not effective or effective[0].get("event") != "meta":
            raise ValueError(
                f"首帧必须是 meta（握手），实际: {effective[0].get('event') if effective else '<空>'}"
            )
        metas = [f for f in effective if f.get("event") == "meta"]
        if len(metas) > 1:
            raise ValueError(f"meta 帧必须唯一，出现 {len(metas)} 次")

    terminals = [
        (i, f) for i, f in enumerate(effective) if f.get("event") in TERMINAL_EVENTS
    ]
    if terminals:
        i, term = terminals[-1]
        if len(terminals) > 1:
            raise ValueError(
                f"终帧（done/error）必须唯一，出现 {len(terminals)} 次"
            )
        if i != len(effective) - 1:
            raise ValueError(
                f"终帧 {term.get('event')!r} 之后仍有 "
                f"{len(effective) - 1 - i} 帧（终帧必须是最后一帧）"
            )
    elif require_meta:
        # HTTP 层完整流必须有终帧；runner 层截断流（require_meta=False）
        # 允许无终帧（采集点可能在终帧前）
        raise ValueError("完整事件流缺少终帧（done/error）")

    return effective
