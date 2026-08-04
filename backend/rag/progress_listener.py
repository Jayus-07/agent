"""ProgressListener — 订阅 TraceCollector span 事件，SSE 推送索引进度。

从 app/api/routes/rag.py 抽出（PR-2.x 路由瘦身）。
"""
from backend.shared.logger import logger


class ProgressListener:
    """订阅 TraceCollector 的 span end 事件，把 indexer 的 9 个标准 span
    映射到前端 SSE 阶段 (loading/parsing/cleaning/dedup/chunking/metadata/embedding/writing)。

    Phase 1.5: 替代原 _ProgressIndexingWrapper 的 monkey-patch，直接订阅
    TraceCollector 事件，避免与 indexer 内部逻辑耦合。
    """

    # span_id → SSE stage 映射；前端用 9 阶段展示，元数据也单独 emit
    SPAN_STAGE_MAP = {
        "index_load":      "loading",
        "index_parse":     "parsing",
        "index_clean":     "cleaning",
        "index_dedup":     "dedup",
        "index_chunk":     "chunking",
        "index_metadata":  "metadata",
        "index_embed":     "embedding",
        "index_vector_db": "writing",
    }

    # span_id → 前端阶段名（终态 stage_elapsed 的键）
    SPAN_STAGE_KEY = SPAN_STAGE_MAP

    def __init__(self, emit_fn):
        from backend.observability.tracer import trace_collector
        self._emit = emit_fn
        self._unsub = trace_collector.subscribe(self._on_span_end)

    def _on_span_end(self, trace, span):
        stage = self.SPAN_STAGE_MAP.get(span.span_id)
        if not stage:
            return
        msg = self._format_message(span)
        try:
            self._emit(stage, msg, duration_ms=int(span.duration_ms or 0))
        except Exception:
            logger.debug("ProgressListener emit 失败", exc_info=True)

    @staticmethod
    def _format_message(span) -> str:
        """根据 span_id + metrics 构造前端可读进度文案。"""
        m = span.metrics or {}
        if span.span_id == "index_load":
            return f"加载 {m.get('file_size', 0)} 字节"
        if span.span_id == "index_parse":
            return f"已解析 {m.get('doc_count', 0)} 页"
        if span.span_id == "index_clean":
            return f"清洗 {m.get('docs_cleaned', 0)} 篇"
        if span.span_id == "index_dedup":
            return "命中缓存" if m.get('cached') else "新建索引"
        if span.span_id == "index_chunk":
            kept = m.get("kept_chunks", 0)
            filtered = m.get("filtered_out", 0)
            return f"切分 {kept} chunks" + (f"（过滤 {filtered}）" if filtered else "")
        if span.span_id == "index_metadata":
            return f"元数据抽取 {m.get('doc_type', '')}"
        if span.span_id == "index_embed":
            succ = m.get("succeeded", 0)
            attempted = m.get("attempted", 0)
            return f"Embedding {succ}/{attempted}" + ("（部分失败）" if succ < attempted else "")
        if span.span_id == "index_vector_db":
            return f"写入 {m.get('written', 0)} 向量"
        return ""

    def unsub(self):
        self._unsub()
