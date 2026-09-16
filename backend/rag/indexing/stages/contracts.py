"""索引管线阶段产物契约（Stage Contracts）。

阶段间不再靠散落的 dict 键名隐式耦合，统一用 dataclass 表达
"上一阶段交给下一阶段的产物"。存量 dict 契约（metadata dict /
向量列表 / SyncResult）在委托层保持不变，dataclass 通过 to_dict()
与其互转 —— 下游零感知，新代码直接消费强类型产物。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from backend.observability.tracer import trace_collector
from backend.shared.logger import logger


# ============================================================
# 阶段产物 dataclass
# ============================================================

@dataclass
class StageContext:
    """贯穿整条管线的上下文（文件身份信息，各阶段只读）。"""
    doc_id: str = ""
    kb_id: str = ""
    department: str = ""
    file_path: str = ""
    file_hash: str = ""
    source_file: str = ""


@dataclass
class ParsedDocument:
    """parse 阶段产物：全文 + 原始 chunk。"""
    full_text: str = ""
    chunks: list = field(default_factory=list)   # list[Document]
    parser_name: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChunkFilterResult:
    """chunk_filter 阶段产物：保留/拒绝统计。"""
    kept: list = field(default_factory=list)
    rejected_count: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class DocMetadataArtifact:
    """metadata 阶段产物：与存量 metadata dict 同构（键集契约见
    MetadataStage.build 的返回值），包装为 dataclass 便于类型标注
    与阶段间传递；to_dict() 保证下游（chunk 注入、doc_db、registry）
    拿到的仍是原 dict。"""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.fields)

    @classmethod
    def from_dict(cls, d: dict) -> "DocMetadataArtifact":
        return cls(fields=dict(d))


@dataclass
class EmbeddingArtifact:
    """embed 阶段产物：成功向量 + 缓存命中统计。"""
    vectors: list = field(default_factory=list)
    cache_hits: int = 0
    cache_total: int = 0


@dataclass
class PersistResult:
    """persist 阶段产物：落库回执。"""
    doc_id: str = ""
    chunk_ids: list[str] = field(default_factory=list)
    near_dup: bool = False
    timings: dict[str, float] = field(default_factory=dict)


# ============================================================
# trace span 收口
# ============================================================

class _SpanHandle:
    """stage_span 的 yield 句柄：阶段内往 span 上挂 metrics/output，
    退出时统一 end_span。"""

    __slots__ = ("span_id", "metrics", "output")

    def __init__(self, span_id: Any):
        self.span_id = span_id
        self.metrics: dict = {}
        self.output: Any = None


@contextmanager
def stage_span(parent_id: str, key: str, name: str, kind: str,
               span_type: str = "llm", input_data: dict | None = None):
    """Stage 装饰器（上下文管理器形态）：统一 span 生命周期。

    - parent_id 为空 → 静默（yield None），与既有 `if parent_span_id:`
      分支语义一致；
    - 正常退出 → end_span(handle.metrics, handle.output)；
    - 异常退出 → end_span(status="error", metrics={"error": ...}) 后
      原样抛出 —— 修复既有代码里异常路径 span 泄漏的问题。

    用法::

        with stage_span(parent_span_id, 'quality', "Quality check",
                        SpanKind.INDEX_QUALITY_CHECK.value) as span:
            quality = assess_quality(full_text)
            if span is not None:
                span.metrics = {"score": quality["score"]}
    """
    if not parent_id:
        yield None
        return

    kwargs: dict = {"parent_id": parent_id, "name": name,
                    "type": span_type, "kind": kind}
    if input_data is not None:
        kwargs["input"] = input_data
    span_id = trace_collector.start_span(key, **kwargs)
    handle = _SpanHandle(span_id)
    try:
        yield handle
    except Exception as e:
        try:
            trace_collector.end_span(span_id,
                metrics={"error": str(e)[:120]}, status="error")
        except Exception:  # pragma: no cover - 上报失败不影响主流程
            logger.debug("[stage_span] error 路径 end_span 失败", exc_info=True)
        raise
    else:
        try:
            trace_collector.end_span(span_id,
                metrics=handle.metrics or None, output=handle.output)
        except Exception:  # pragma: no cover
            logger.debug("[stage_span] end_span 失败", exc_info=True)
