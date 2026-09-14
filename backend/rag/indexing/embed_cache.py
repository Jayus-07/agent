"""Embedding 结果缓存（3.1）— Redis 层，键 = 模型 + 文本 sha256。

收益场景：重索引/局部修改文档时，未变化 chunk（含 Contextual Prefix 的
最终嵌入文本）免重复嵌入——嵌入是索引链路最贵的计算。

设计：
- 键 `rag:emb:{model}:{sha256(embed_text)}`——embed_text 是含前缀的最终
  文本，前缀变化自然产生新键，无脏读；
- 值 JSON 数组（float 列表）；TTL 默认 30 天；
- Redis 不可用/未配置 → 全部 miss + 写入 no-op，主流程零影响；
- 开关 `RAG_EMBED_CACHE_ENABLED`（默认 on）。
"""
from __future__ import annotations

import hashlib
import json

from backend.shared.logger import logger


class EmbeddingCache:
    """批量 get/put 的 embedding Redis 缓存读写器（无状态，可每实例新建）。"""

    def __init__(self, model_name: str, enabled: bool = True,
                 ttl_seconds: int = 30 * 86400):
        from backend.config.rag import RAG_EMBED_CACHE_ENABLED, RAG_EMBED_CACHE_TTL_SECONDS
        self.model_name = (model_name or "").strip() or "unknown"
        self.enabled = enabled and RAG_EMBED_CACHE_ENABLED
        self.ttl = ttl_seconds or RAG_EMBED_CACHE_TTL_SECONDS
        self.prefix = f"rag:emb:{self.model_name}:"

    @staticmethod
    def _key(prefix: str, text: str) -> str:
        return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _client(self):
        if not self.enabled:
            return None
        try:
            from backend.infra.redis.client import get_redis
            return get_redis()
        except Exception as e:  # pragma: no cover - Redis 基建异常按 miss 处理
            logger.debug(f"[EmbedCache] Redis 获取失败（按 miss 处理）: {e}")
            return None

    def get_many(self, texts: list[str]) -> list:
        """批量查询。返回与 texts 等长的列表，命中为向量，未命中为 None。"""
        r = self._client()
        if r is None or not texts:
            return [None] * len(texts)
        keys = [self._key(self.prefix, t) for t in texts]
        try:
            pipe_values = r.mget(keys)
        except Exception as e:
            logger.debug(f"[EmbedCache] mget 失败（按 miss 处理）: {e}")
            return [None] * len(texts)
        out: list = []
        for v in pipe_values:
            if v is None:
                out.append(None)
                continue
            try:
                if isinstance(v, bytes):
                    v = v.decode("utf-8")
                vec = json.loads(v)
                out.append(vec if isinstance(vec, list) else None)
            except (ValueError, TypeError):
                out.append(None)
        return out

    def put_many(self, texts: list[str], vectors: list) -> None:
        """批量回填（软失败：写缓存异常不影响主流程）。"""
        r = self._client()
        if r is None or not texts or len(texts) != len(vectors):
            return
        try:
            pipe = r.pipeline(transaction=False)
            for t, vec in zip(texts, vectors):
                if vec is None:
                    continue
                pipe.setex(
                    self._key(self.prefix, t), self.ttl,
                    json.dumps(vec, ensure_ascii=False),
                )
            pipe.execute()
        except Exception as e:
            logger.debug(f"[EmbedCache] 回填失败（不影响主流程）: {e}")
