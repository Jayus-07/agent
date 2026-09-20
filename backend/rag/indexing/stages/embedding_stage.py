"""Embedding Stage — 批量嵌入 + 重试 + 缓存 + contextual prefix。

从 indexer.py 原文迁移（行为零改动），IncrementalIndexer 上的
_embed_* 方法保留为薄委托。span 语义保留：批失败降级逐条时
每 chunk 单独 child span。
"""

from __future__ import annotations

import os
import random
import time

from backend.config.rag import (
    EMBED_BATCH_SIZE,
    EMBED_RETRY_MAX,
    EMBED_RETRY_BACKOFF_BASE,
    EMBED_RETRY_BACKOFF_MAX,
)
from backend.observability.tracer import trace_collector, SpanKind
from backend.shared.logger import logger


def embed_backoff_seconds(attempt: int) -> float:
    """第 attempt 次重试（0 起）前的退避秒数：指数退避 + 随机抖动。

    抖动防止多文档并发索引时所有批次同拍重试（thundering herd）。
    常量读本模块全局（测试 monkeypatch 本模块的 EMBED_RETRY_BACKOFF_BASE 清零）。
    """
    base = min(EMBED_RETRY_BACKOFF_BASE ** (attempt + 1), EMBED_RETRY_BACKOFF_MAX)
    return base + random.uniform(0, 1.0)


class EmbeddingStage:
    """embedding 阶段：持有 embedding 实例与实例级缓存，无 registry 依赖。"""

    def __init__(self, embedding):
        self._embedding = embedding
        self._cache = None  # 懒创建（cache()）

    # ---- contextual prefix ----

    @staticmethod
    def text_for(chunk, doc_summary: str = "") -> str:
        """构造 embedding 文本：Contextual Prefix + 正文（1.2）。

        三级前缀（字段缺失自动跳过对应段，全缺则纯正文）：
          1.【文档】文档级摘要前 100 字——contextual retrieval 的零 LLM 版：
            复用元数据阶段已生成的 summary，为 chunk 补文档级上下文
          2.【章节】chunk 所属章节标题（切分策略/indexer 章节映射已注入）
          3.【相关问题】模拟问题（Document Expansion，question_gen 产出）
        """
        parts: list[str] = []
        if doc_summary:
            parts.append("【文档】" + doc_summary.strip()[:100])
        section_title = (chunk.metadata.get("section_title") or "").strip()
        if section_title:
            parts.append("【章节】" + section_title)
        questions = chunk.metadata.get("simulated_questions") or []
        if questions:
            parts.append("【相关问题】" + " | ".join(questions))
        # 4.3c: 表格行 LLM 描述——kv 数据行的自然语言语义
        table_desc = (chunk.metadata.get("table_desc") or "").strip()
        if table_desc:
            parts.append("【表格】" + table_desc)
        prefix = "\n".join(parts)
        if prefix:
            return prefix + "\n\n" + chunk.page_content
        return chunk.page_content

    # ---- 逐条 + 批量嵌入 ----

    def single_with_retry(self, i: int, chunk, embed_text: str):
        """逐条 embedding（含重试 + 失败/重试 span），成功返回向量，失败返回 None。

        供批量化降级路径与不支持 embed_documents 的 embedding 实现复用，
        保留旧版"逐 chunk 失败 span"语义。
        """
        last_err = None
        for attempt in range(EMBED_RETRY_MAX):
            try:
                vec = self._embedding.embed_query(embed_text)
                if attempt > 0:
                    chunk_span = trace_collector.start_span(
                        f"embed_chunk_{i}",
                        parent_id="index_embed",
                        name=f"Embed chunk {i} retried",
                        type="embedding",
                        kind=SpanKind.INDEX_EMBED.value,
                        input={"chunk_index": i,
                               "doc_id": chunk.metadata.get("doc_id", "")},
                    )
                    chunk_span.retry_count = attempt
                    trace_collector.end_span(
                        chunk_span,
                        metrics={"attempt": attempt + 1,
                                 "retry_count": attempt},
                    )
                return vec
            except Exception as e:
                last_err = e
                # 指数退避 + 抖动：立即连打对抖动的远程服务是雪崩式重试
                if attempt < EMBED_RETRY_MAX - 1:
                    time.sleep(embed_backoff_seconds(attempt))
        # 所有重试都失败 → 创建 child span 记录失败
        logger.error(f"[Embed] chunk {i} 嵌入失败 {EMBED_RETRY_MAX} 次: {last_err}")
        chunk_span = trace_collector.start_span(
            f"embed_chunk_{i}",
            parent_id="index_embed",
            name=f"Embed chunk {i} FAILED",
            type="embedding",
            kind=SpanKind.INDEX_EMBED.value,
            input={"chunk_index": i,
                   "doc_id": chunk.metadata.get("doc_id", "")},
        )
        chunk_span.retry_count = EMBED_RETRY_MAX
        trace_collector.end_span(chunk_span, status="error",
            metrics={"error": str(last_err)[:100] if last_err else "unknown",
                     "retry_count": EMBED_RETRY_MAX})
        return None

    def batch_with_retry(self, chunks, parent_span, doc_summary: str = "") -> list:
        """批量嵌入（P2 批量化）；成功静默，失败单独 child span 记录。

        - 每批 EMBED_BATCH_SIZE 条调 embed_documents：本地模型批推理走矩阵
          运算，比逐条 embed_query 快数倍（对齐 chunking._embed_sentences_batched 模式）
        - 每批重试 EMBED_RETRY_MAX 次；耗尽重试的批降级逐条 embed_query，
          隔离失败点，保留逐 chunk 失败 span 语义
        - embedding 实现无 embed_documents → 直接逐条路径
        - doc_summary：1.2 Contextual Prefix——文档级摘要拼入每条嵌入文本

        Returns: 成功嵌入的向量列表（失败的 chunk 不在此列）。

        P1: 每个 chunk 在 embedding 前拼接"模拟问题前缀"（Document Expansion），
        召回率 +10-15%（口语化提问 ↔ 书面文档的语义鸿沟）。
        """
        if not chunks:
            return []
        texts = [self.text_for(c, doc_summary=doc_summary) for c in chunks]
        succeeded: list = []
        cache = self.cache()
        cache_hits = 0

        batch_embed = getattr(self._embedding, "embed_documents", None)
        if not callable(batch_embed):
            for i, chunk in enumerate(chunks):
                vec = None
                cached = cache.get_many([texts[i]])[0] if cache.enabled else None
                if cached is not None:
                    vec = cached
                    cache_hits += 1
                else:
                    vec = self.single_with_retry(i, chunk, texts[i])
                    if vec is not None:
                        cache.put_many([texts[i]], [vec])
                if vec is not None:
                    succeeded.append(vec)
            self.report_cache_metrics(parent_span, cache_hits, len(chunks))
            return succeeded

        # 批大小：优先取 embedding 实现声明的最优批（cloud 模式受 DashScope
        # 单请求上限约束，直接取上限避免外层大批被内部再拆）；实现未声明
        # （测试 fake / 非标准包装）或非法时回退配置批大小。
        _declared = getattr(self._embedding, "embed_batch_size", None)
        batch_size = _declared if isinstance(_declared, int) and _declared > 0 else EMBED_BATCH_SIZE
        for start in range(0, len(chunks), batch_size):
            batch_chunks = chunks[start:start + batch_size]
            batch_texts = texts[start:start + batch_size]
            batch_vecs: list = [None] * len(batch_texts)
            # 3.1: 缓存优先——命中的位置直接填充，只对 miss 的子集真实嵌入
            if cache.enabled:
                cached = cache.get_many(batch_texts)
                for i, v in enumerate(cached):
                    if v is not None:
                        batch_vecs[i] = v
            miss_positions = [i for i, v in enumerate(batch_vecs) if v is None]
            cache_hits += len(batch_texts) - len(miss_positions)
            last_err = None
            batch_ok = False
            if miss_positions:
                miss_texts = [batch_texts[i] for i in miss_positions]
                for _attempt in range(EMBED_RETRY_MAX):
                    try:
                        vecs = batch_embed(miss_texts)
                        if not isinstance(vecs, (list, tuple)) or len(vecs) != len(miss_texts):
                            raise ValueError(
                                f"embed_documents 返回非法: type={type(vecs).__name__}, "
                                f"期望 {len(miss_texts)} 条向量"
                            )
                        for i, v in zip(miss_positions, vecs):
                            batch_vecs[i] = v
                        cache.put_many(miss_texts, list(vecs))
                        batch_ok = True
                        break
                    except Exception as e:
                        last_err = e
                        # 指数退避 + 抖动（整批重试路径）
                        if _attempt < EMBED_RETRY_MAX - 1:
                            time.sleep(embed_backoff_seconds(_attempt))
                if batch_ok:
                    succeeded.extend(v for v in batch_vecs if v is not None)
                    continue
                # 整批（miss 子集）耗尽重试 → 降级逐条，隔离单点失败（旧语义保留）
                logger.warning(
                    f"[Embed] 批次 {start // batch_size}（{len(miss_texts)}/{len(batch_texts)} chunks，"
                    f"其余命中缓存）重试 {EMBED_RETRY_MAX} 次全失败 ({last_err})，降级逐条"
                )
                for i in miss_positions:
                    vec = self.single_with_retry(start + i, batch_chunks[i], batch_texts[i])
                    if vec is not None:
                        batch_vecs[i] = vec
                        cache.put_many([batch_texts[i]], [vec])
            succeeded.extend(v for v in batch_vecs if v is not None)
        self.report_cache_metrics(parent_span, cache_hits, len(texts))
        return succeeded

    # ---- 缓存与指标 ----

    def cache(self):
        """3.1: embedding 结果缓存读写器（实例级懒创建）。"""
        if self._cache is None:
            from backend.rag.indexing.embed_cache import EmbeddingCache
            model_candidates = (
                getattr(self._embedding, "model_name", None),
                getattr(self._embedding, "_model_name", None),
            )
            model_name = next(
                (item for item in model_candidates if isinstance(item, str) and item),
                "",
            )
            if not model_name:
                raw_model = getattr(self._embedding, "model", None)
                if isinstance(raw_model, str) and raw_model:
                    model_name = os.path.basename(raw_model)
            model_name = model_name or "unknown"
            provider_candidates = (
                getattr(self._embedding, "_provider", None),
                getattr(self._embedding, "provider", None),
            )
            provider = next(
                (item for item in provider_candidates if isinstance(item, str) and item),
                "",
            )
            revision_candidates = (
                getattr(self._embedding, "_model_revision", None),
                getattr(self._embedding, "model_revision", None),
            )
            model_revision = next(
                (item for item in revision_candidates if isinstance(item, str) and item),
                "",
            )
            try:
                from backend.config import model_roles

                effective = model_roles.resolve_effective("embedding")
                provider = provider or str(effective.get("provider") or "")
                model_revision = model_revision or str(
                    effective.get("updated_at") or effective.get("source") or ""
                )
                if not provider and model_name != "unknown":
                    from backend.infra.llm.models import resolve_provider

                    provider = resolve_provider(model_name)
            except Exception as exc:
                logger.debug("[Embed] 读取缓存模型版本失败，使用运行时身份: %s", exc)
            self._cache = EmbeddingCache(
                model_name,
                provider=provider,
                model_revision=model_revision,
            )
        return self._cache

    @staticmethod
    def report_cache_metrics(parent_span, hits: int, total: int) -> None:
        if hits <= 0 or total <= 0:
            return
        logger.info(f"[Embed] 缓存命中 {hits}/{total}")
        try:
            if parent_span is not None and hasattr(parent_span, "metrics"):
                parent_span.metrics["embedding_cache_hit"] = hits
                parent_span.metrics["embedding_cache_miss"] = total - hits
        except Exception:  # pragma: no cover - metrics 写失败不影响主流程
            pass
