"""RAG 答案缓存 — 相同查询命中缓存跳过 LLM 生成（~4.8s）。

缓存策略：
  - Key = sha256("v2|" + normalized_query | kb_id | cache_version | filter_hash | model | scope)
    （STOP CS-A P0-4：key namespace 升 v2 —— v1 值是裸答案字符串、无 meta，
    消费方 CS Knowledge Gate 依赖 meta 判定置信度，命中 v1 只能视为 miss）
  - Value = 结构化记录 {"schema_version": 2, "answer", "meta", "created_at"}
    meta 是 Evidence Gate 的判定依据（confidence / can_answer / answer_status /
    sources），缓存命中必须返回与首算一致的判定语义，禁止伪造可信度
  - TTL = 1 小时（文档更新后通过 invalidate_kb 失效）
  - 多轮对话不走缓存（chat_history 非空时跳过）
  - 文档上传/删除时调用 invalidate_kb(kb_id) 使该 KB 所有缓存失效

失效机制：
  - Redis INCR cache_version:{kb_id} 原子递增版本号
  - 版本号变化 → 所有旧 key 的 hash 值失效 → 自动 miss
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from backend.observability.log_privacy import query_preview
from backend.shared.logger import logger

# 版本号本地缓存 TTL（秒）：get/put 每次都查 Redis 版本号会多 1-2 个 RTT，
# 版本号极少变化，本地短 TTL 缓存即可；代价是跨进程失效最多延迟该时长生效
_VERSION_CACHE_TTL = 10.0

# STOP CS-A P0-4：缓存记录 schema 版本。v1 = 裸答案字符串（已废弃，读到
# 一律视为 miss 重新计算，绝不把无 meta 的旧答案升级成"可信"结果）。
CACHE_SCHEMA_VERSION = 2
_KEY_NAMESPACE = "v2"


def _coerce_record(value: Any) -> dict | None:
    """把缓存值归一为 v2 记录；v1 字符串/畸形结构一律视为 miss。"""
    if not isinstance(value, dict):
        return None
    if int(value.get("schema_version") or 0) != CACHE_SCHEMA_VERSION:
        return None
    answer = value.get("answer")
    if not isinstance(answer, str):
        return None
    meta = value.get("meta")
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "answer": answer,
        "meta": meta if isinstance(meta, dict) else {},
        "created_at": str(value.get("created_at") or ""),
    }


class AnswerCache:
    """RAG 答案缓存封装。"""

    def __init__(self, ttl: int = 3600):
        """初始化答案缓存。

        Args:
            ttl: 缓存有效期（秒），默认 1 小时
        """
        self._ttl = ttl
        self._cache = None
        # 本地版本号缓存：{kb_id: (version, monotonic_ts)}
        self._version_cache: dict[str, tuple[int, float]] = {}

    def _get_cache(self):
        """惰性获取统一缓存实例（避免模块导入时 Redis 未就绪）。"""
        if self._cache is None:
            from backend.infra.cache import get_cache
            self._cache = get_cache("rag_answer", ttl=self._ttl)
        return self._cache

    def _build_key(
        self,
        query: str,
        kb_id: str,
        metadata_filter: dict,
        model: str,
        scope: str = "",
    ) -> str:
        """构建缓存 key。

        Key = sha256(normalized_query | kb_id | cache_version | filter_hash | model | scope)

        2026-09-15 补 scope：主体授权过滤发生在检索之后（ChunkLevelRetriever
        按 subject_type/department 剔除越界文档），因此不同主体的"同一个问题"
        可以有合法的不同答案。此前键不含身份维度，实测 customer 主体的拒答
        答案污染了 employee 的缓存条目（在线服务里表现为"越权缓存污染"）。
        scope 缺省空串 = 历史行为（无主体信息路径）。

        STOP CS-A P0-4：key namespace ``v2`` —— 与 v1 裸字符串值物理隔离，
        旧条目自然过期，不做任何原地升级。
        """
        normalized = query.strip().lower()
        cache_version = self._get_cache_version(kb_id)
        filter_hash = self._hash_filter(metadata_filter)

        raw = (
            f"{_KEY_NAMESPACE}|{normalized}|{kb_id}|{cache_version}"
            f"|{filter_hash}|{model}|{scope}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cache_version(self, kb_id: str) -> int:
        """读取指定 KB 的缓存版本号（带本地短 TTL 缓存，减少 Redis RTT）。

        本进程内的 invalidate_kb 会同步更新本地缓存，立即生效；
        其他进程的失效最多延迟 _VERSION_CACHE_TTL 秒被本进程感知。
        """
        local = self._version_cache.get(kb_id)
        if local is not None:
            version, ts = local
            if time.monotonic() - ts < _VERSION_CACHE_TTL:
                return version
        try:
            version = self._get_cache().get_json(f"__ver__:kb:{kb_id}")
            version = int(version) if version else 0
        except Exception as e:
            logger.debug(f"[AnswerCache] 读取版本号失败: {e}")
            return local[0] if local else 0
        self._version_cache[kb_id] = (version, time.monotonic())
        return version

    @staticmethod
    def _hash_filter(metadata_filter: dict) -> str:
        """对 metadata_filter 生成稳定 hash（忽略顺序）。"""
        if not metadata_filter:
            return "empty"
        try:
            serialized = json.dumps(metadata_filter, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(serialized.encode()).hexdigest()[:12]
        except Exception:
            return "error"

    def get(
        self,
        query: str,
        kb_id: str,
        metadata_filter: dict,
        model: str,
        scope: str = "",
    ) -> dict | None:
        """查询缓存。命中返回 v2 记录 dict（answer+meta），未命中返回 None。

        STOP CS-A P0-4：v1 裸字符串值 / 畸形结构 / 版本不符 → 视为 miss
        （重新计算，绝不把旧答案包装成可信结果）。scope=主体授权范围。
        """
        try:
            key = self._build_key(query, kb_id, metadata_filter, model, scope)
            cached = self._get_cache().get_json(key)
            record = _coerce_record(cached)
            if record is not None:
                logger.debug(
                    f"[AnswerCache] 命中: query={query_preview(query)} kb={kb_id}"
                )
                return record
            return None
        except Exception as e:
            logger.debug(f"[AnswerCache] 查询失败: {e}")
            return None

    def put(
        self,
        query: str,
        kb_id: str,
        metadata_filter: dict,
        model: str,
        answer: str,
        scope: str = "",
        meta: dict | None = None,
    ) -> None:
        """写入缓存。scope=主体授权范围（与 get 必须一致）。

        STOP CS-A P0-4：缓存值为结构化 v2 记录（answer + Evidence Gate
        依赖的完整 meta），命中方据此还原与首算一致的置信度语义。
        """
        try:
            key = self._build_key(query, kb_id, metadata_filter, model, scope)
            record = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "answer": answer,
                "meta": dict(meta or {}),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._get_cache().set_json(key, record, ttl=self._ttl)
            logger.debug(f"[AnswerCache] 写入: query={query[:60]}... kb={kb_id}")
        except Exception as e:
            logger.debug(f"[AnswerCache] 写入失败: {e}")

    def invalidate_kb(self, kb_id: str) -> None:
        """使指定 KB 的所有缓存失效（原子递增版本号）。"""
        try:
            new_version = self._get_cache().incr_version(f"kb:{kb_id}")
            # 同步更新本地版本缓存，本进程后续 get/put 立即使用新版本
            self._version_cache[kb_id] = (int(new_version), time.monotonic())
            logger.info(f"[AnswerCache] 失效: kb={kb_id} new_version={new_version}")
        except Exception as e:
            logger.warning(f"[AnswerCache] 失效失败: {e}")


_answer_cache: AnswerCache | None = None


def get_answer_cache() -> AnswerCache:
    """获取 AnswerCache 单例。"""
    global _answer_cache
    if _answer_cache is None:
        _answer_cache = AnswerCache()
    return _answer_cache
