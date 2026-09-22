"""context_budget.models — 上下文预算数据模型

基础版数据模型（规格见 docs/2026-09-22-context-budget-management-实施规格.md）：

- ContextUsage:     上下文用量快照（前端 / 业务层不自行重复计算）
- ToolResultRef:    L1 工具结果引用（preview + 未来 ArtifactStore 的 artifact_id 预留）
- PreparedContext:  统一 Prompt Preflight 的产出（L2/L3 处理后的 active context）
- ArtifactStore:    未来落盘接口 Protocol（本版不实现存储，仅约束签名）
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import BaseMessage


@dataclass(frozen=True)
class ContextUsage:
    """一次上下文用量快照。usage_ratio ∈ [0, ∞)（>1 = 已超预算）。"""

    used_tokens: int
    input_budget: int
    remaining_tokens: int
    usage_ratio: float

    def to_dict(self) -> dict:
        return {
            "used_tokens": self.used_tokens,
            "input_budget": self.input_budget,
            "remaining_tokens": self.remaining_tokens,
            "usage_ratio": round(self.usage_ratio, 4),
        }


@dataclass
class ToolResultRef:
    """L1 工具结果引用。

    基础版只有 preview（artifact_id=None）；未来可扩展 PG context_artifacts /
    MinIO 等共享存储，业务层必须依赖本结构而不是"永远只有 preview"的假设。
    """

    original_tokens: int
    inline_tokens: int
    preview: str
    truncated: bool
    artifact_id: str | None = None

    def to_payload(self) -> dict:
        """转成写入 step_results["output"] 的预览结构。"""
        return {
            "context_compacted": True,
            "type": "tool_result_preview",
            "original_tokens": self.original_tokens,
            "preview": self.preview,
            **({"artifact_id": self.artifact_id} if self.artifact_id else {}),
        }


@dataclass
class PreparedContext:
    """prepare_llm_context 的产出：裁剪/压缩后的 active context + 用量。

    overflow=True 表示经过全部确定性裁剪后仍超 hard budget（已记 warning +
    metric，调用方拿到的是最大程度压缩后的结果，属安全降级而非静默超限）。
    """

    messages: list[BaseMessage] = field(default_factory=list)
    previous_outputs: dict[str, Any] = field(default_factory=dict)
    rag_context: list[str] | None = None
    usage: ContextUsage | None = None
    overflow: bool = False


@runtime_checkable
class ArtifactStore(Protocol):
    """未来 L1 落盘接口预留（本版不实现）。

    约束：实现必须跨容器可见（PG 大字段 / 共享对象存储），禁止本地临时文件
    —— app 与 worker 容器磁盘不共享（现状说明 §6.2）。
    """

    async def save(self, key: str, content: str) -> str:
        """保存完整内容，返回 artifact_id。"""
        ...

    async def get(self, artifact_id: str) -> str:
        """按 artifact_id 取回完整内容。"""
        ...
