"""PgVectorKnowledgeStore — 向量知识库的 PostgreSQL + pgvector 实现。

迁移计划 2026-09-17「Chroma → pgvector」落地（方案见
docs/chroma-pgvector迁移方案-2026-09-17.md）。与 ChromaKnowledgeStore
对外接口完全一致（KnowledgeStore ABC），仅替换存储层：
Chroma 本地目录 → PG rag_vectors 表（collection 列区分）。

设计要点：
  - persist_directory 语义映射为 collection 名（路径 basename），
    与既有 Chroma 实例目录一一对应（data/chroma → "chroma"）。
  - embedding 固定 1024 维（text-embedding-v3，env VECTOR_PG_DIM 可覆盖）；
    bge-small-zh-v1.5 本地轨（512 维）已确认弃用，不建第二张表。
  - 确定性 ID：`{collection}:{doc_id|md5(content)[:12]}:{chunk_index|批内序号}`
    → 同批内容重跑幂等（INSERT ... ON CONFLICT DO UPDATE）。
  - 距离量纲：pgvector `<=>` cosine distance = 1 - cos_sim ∈ [0,2]，
    与 Chroma cosine 距离一致，下游分数消费方无需改动。
  - filter：接受与 Chroma 相同的 where 语法（含 normalize_where 输出形态），
    由 where_to_sql() 翻译为 JSONB 参数化 SQL（纯函数，可独立单测）。
  - 引擎开关：backend/config/database.py::VECTOR_BACKEND（env VECTOR_BACKEND），
    工厂分发见 factory.py；默认 chroma，合入后行为零变化。

连接层风格与 rag/indexing/chunk_store_pg.py 一致（psycopg2 短连接 + 幂等 DDL）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import psycopg2
import psycopg2.extras

from backend.config.database import VECTOR_PG_CONFIG
from backend.rag.vectorstore.knowledge_store import (
    KnowledgeStore,
    _sanitize_metadata,
    normalize_where,
)
from backend.shared.logger import logger

# 嵌入维度（text-embedding-v3 = 1024）。env 逃生口仅供测试/未来换模型，日常勿动。
EMBEDDING_DIM = int(os.getenv("VECTOR_PG_DIM", "1024"))

# 同进程避免重复 DDL（chunk 级 + doc 级等多次构造）
_DDL_DONE: set[str] = set()
_DDL_LOCK = threading.Lock()


# ======================= where → SQL 翻译器（纯函数） =======================

def _scalar_to_text(v: Any) -> str:
    """检索值 → JSONB `->>` 文本比较形态。

    与写入端 _sanitize_metadata 对齐：bool → JSON true/false → 文本 'true'/'false'；
    数字 → str（1 → '1'、1.0 → '1.0'，与 jsonb 文本化一致）；None → ''（写入端语义）。
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _cond_sql(key: str, value: Any, params: list) -> str:
    """单条件 → SQL 片段。key 为 metadata 字段名或 $contains。"""
    if key == "$contains":
        # Chroma $contains 是文档内容子串匹配
        params.append(f"%{value}%")
        return "content ILIKE %s"
    if isinstance(value, dict):
        op, v = next(iter(value.items()))
        if op == "$eq":
            return _cond_sql(key, v, params)
        if op == "$ne":
            # 缺失字段视为 ≠（与 Chroma where 对缺失字段的宽松语义取宽侧，注释备案）
            params.append(json.dumps({key: v}, ensure_ascii=False))
            return "NOT (metadata @> %s::jsonb)"
        if op in ("$gt", "$gte", "$lt", "$lte"):
            sql_op = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}[op]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                params.append(v)
                return f"(metadata->>'{key}')::numeric {sql_op} %s"
            params.append(_scalar_to_text(v))
            return f"metadata->>'{key}' {sql_op} %s"
        if op == "$in":
            vals = [_scalar_to_text(x) for x in (v or [])]
            params.append(vals)
            return f"metadata->>'{key}' = ANY(%s)"
        if op == "$nin":
            vals = [_scalar_to_text(x) for x in (v or [])]
            params.append(vals)
            return f"NOT (metadata->>'{key}' = ANY(%s))"
        raise ValueError(f"不支持的 Chroma where 操作符: {op}")
    # 标量 → JSONB 包含（GIN 索引命中；类型敏感：number 1 与 text "1" 不互配，
    # 与 Chroma metadata 类型语义一致）
    params.append(json.dumps({key: "" if value is None else value}, ensure_ascii=False))
    return "metadata @> %s::jsonb"


def _node_sql(node: dict, params: list) -> str:
    """where 节点 → SQL 片段。支持 $and/$or 递归与 flat 多键（兜底 AND 组合）。"""
    if not node:
        return ""
    if "$and" in node:
        parts = [p for p in (_node_sql(n, params) for n in node["$and"]) if p]
        return "(" + " AND ".join(parts) + ")" if parts else ""
    if "$or" in node:
        parts = [p for p in (_node_sql(n, params) for n in node["$or"]) if p]
        return "(" + " OR ".join(parts) + ")" if parts else ""
    if len(node) == 1:
        k, v = next(iter(node.items()))
        return _cond_sql(k, v, params)
    # flat 多键兜底（normalize_where 已保证 $and 包裹，此处防御性支持）
    parts = [_cond_sql(k, v, params) for k, v in node.items()]
    return "(" + " AND ".join(parts) + ")"


def where_to_sql(where: dict | None, params: list) -> str:
    """Chroma where 语法 → 参数化 SQL 片段；params 就地追加占位符参数。

    输入兼容 flat dict 与 normalize_where() 的 $and 包裹形态。
    返回空串表示无过滤条件。
    """
    if not where:
        return ""
    return _node_sql(where, params)


# ======================= pgvector 实现 =======================

class PgVectorKnowledgeStore(KnowledgeStore):
    """rag_vectors 表（pgvector）实现的向量知识库。

    `persist_directory` 仅作为 collection 名的来源（basename），
    数据全部落在 PG；collection 列隔离各逻辑库。
    """

    def __init__(self, persist_directory: str, embedding_function: Any):
        self.persist_directory = str(persist_directory)
        self.embedding_function = embedding_function
        self._collection = _collection_name_from_path(persist_directory)
        self._table = os.getenv("VECTOR_PG_TABLE_PREFIX", "") + "rag_vectors"
        self._lock = threading.Lock()
        self._init_db()

    # ---- 工具 ----

    @staticmethod
    def _doc_id_of(meta: dict, text: str) -> str:
        doc_id = meta.get("doc_id")
        if doc_id:
            return str(doc_id)
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    def _static_id(self, meta: dict, text: str, idx: int) -> str:
        """确定性 ID：同内容同 metadata 重跑 → 同 ID（幂等 upsert 基础）。"""
        return f"{self._collection}:{self._doc_id_of(meta, text)}:{meta.get('chunk_index', idx)}"

    # ---- 连接层（仿 chunk_store_pg.py）----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**VECTOR_PG_CONFIG)
        try:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- 建表（幂等）----

    def _init_db(self) -> None:
        if self._table in _DDL_DONE:
            return
        with _DDL_LOCK:
            if self._table in _DDL_DONE:
                return
            with self._lock, self._conn() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception as e:  # 无权限时依赖镜像内已建扩展
                    logger.warning(f"[PgVectorStore] CREATE EXTENSION 跳过: {e}")
                    conn.rollback()
                t = self._table
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {t} (
                        id           TEXT PRIMARY KEY,
                        collection   TEXT NOT NULL,
                        doc_id       TEXT,
                        content      TEXT NOT NULL DEFAULT '',
                        metadata     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding    vector({EMBEDDING_DIM}) NOT NULL,
                        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{t}_hnsw ON {t} "
                    f"USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{t}_scope ON {t} (collection, doc_id)")
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{t}_meta ON {t} USING gin (metadata)")
            _DDL_DONE.add(self._table)
            logger.info(f"[PgVectorStore] 就绪: table={self._table} collection={self._collection}")

    # ---- 工厂方法（签名与 ChromaKnowledgeStore 对齐）----

    @classmethod
    def from_documents(
        cls, documents: list[Any], embedding: Any, persist_directory: str,
        embedding_metadata: dict | None = None,
    ) -> "PgVectorKnowledgeStore":
        """从 Document 列表创建 chunk 级向量库（embedding_metadata 兼容签名，PG 侧落 metadata 表说明列由调用方自管）。"""
        instance = cls(persist_directory=persist_directory, embedding_function=embedding)
        if documents:
            instance.add_documents(documents)
        return instance

    @classmethod
    def from_texts(
        cls, texts: list[str], embedding: Any,
        metadatas: list[dict] | None, persist_directory: str,
        embedding_metadata: dict | None = None,
    ) -> "PgVectorKnowledgeStore":
        instance = cls(persist_directory=persist_directory, embedding_function=embedding)
        if texts:
            instance.add_texts(texts, metadatas)
        return instance

    # ---- 写入 ----

    def _upsert_rows(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        with self._lock, self._conn() as conn:
            psycopg2.extras.execute_values(
                conn.cursor(),
                f"""INSERT INTO {self._table}
                       (id, collection, doc_id, content, metadata, embedding)
                   VALUES %s
                   ON CONFLICT (id) DO UPDATE SET
                       doc_id     = EXCLUDED.doc_id,
                       content    = EXCLUDED.content,
                       metadata   = EXCLUDED.metadata,
                       embedding  = EXCLUDED.embedding""",
                rows,
                template="(%s, %s, %s, %s, %s::jsonb, %s)",
                page_size=500,
            )
        return len(rows)

    def _texts_to_rows(
        self, texts: list[str], metadatas: list[dict] | None,
        embeddings: list | None, explicit_ids: list[str] | None = None,
    ) -> list[tuple]:
        if metadatas is None:
            metadatas = [{} for _ in texts]
        if embeddings is None:
            embeddings = self.embedding_function.embed_documents(texts)
        if len(embeddings) != len(texts):
            raise ValueError(f"embeddings 数量({len(embeddings)})与 texts({len(texts)})不一致")
        rows = []
        for i, (text, meta) in enumerate(zip(texts, metadatas)):
            meta = _sanitize_metadata(dict(meta or {}))
            rid = explicit_ids[i] if explicit_ids else self._static_id(meta, text, i)
            rows.append((
                rid,
                self._collection,
                meta.get("doc_id"),
                text,
                json.dumps(meta, ensure_ascii=False),
                np.asarray(embeddings[i], dtype=np.float32),
            ))
        return rows

    def add_documents(
        self, documents: list[Any], embeddings: list | None = None,
    ) -> list[str]:
        texts = [d.page_content for d in documents]
        metas = [dict(d.metadata or {}) for d in documents]
        rows = self._texts_to_rows(texts, metas, embeddings)
        self._upsert_rows(rows)
        return [r[0] for r in rows]

    def add_texts(
        self, texts: list[str], metadatas: list[dict] | None = None,
    ) -> list[str]:
        rows = self._texts_to_rows(list(texts), metadatas, None)
        self._upsert_rows(rows)
        return [r[0] for r in rows]

    def upsert_texts(
        self, texts: list[str], metadatas: list[dict] | None, ids: list[str],
    ) -> list[str]:
        """显式 ID 覆盖写（对齐 Chroma `_collection.upsert` 语义，MarketIndex 用）。"""
        if not (len(texts) == len(ids) and (metadatas is None or len(metadatas) == len(texts))):
            raise ValueError("texts/metas/ids 长度不一致")
        rows = self._texts_to_rows(list(texts), metadatas, None, explicit_ids=list(ids))
        self._upsert_rows(rows)
        return list(ids)

    def count(self) -> int:
        """当前 collection 文档总数（对齐 Chroma `_collection.count`）。"""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT count(*) FROM {self._table} WHERE collection = %s",
                        (self._collection,))
            return int(cur.fetchone()[0])

    # ---- 查询 ----

    def similarity_search_with_score(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[tuple[Any, float]]:
        from langchain_core.documents import Document

        qvec = np.asarray(self.embedding_function.embed_query(query), dtype=np.float32)
        params: list = [qvec, self._collection]
        where_sql = where_to_sql(normalize_where(filter), params)
        sql = (
            f"SELECT id, content, metadata, embedding <=> %s AS distance "
            f"FROM {self._table} WHERE collection = %s"
        )
        if where_sql:
            sql += f" AND {where_sql}"
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params.extend([qvec, k])
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            out = []
            for _id, content, meta, dist in cur.fetchall():
                out.append((
                    Document(page_content=content or "", metadata=meta or {}),
                    float(dist),
                ))
            return out

    def similarity_search(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[Any]:
        return [doc for doc, _ in self.similarity_search_with_score(query, k=k, filter=filter)]

    def get(self, where: dict | None = None) -> dict:
        params: list = [self._collection]
        where_sql = where_to_sql(normalize_where(where), params)
        sql = f"SELECT id, content, metadata FROM {self._table} WHERE collection = %s"
        if where_sql:
            sql += f" AND {where_sql}"
        sql += " ORDER BY id"
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return {
            "ids": [r[0] for r in rows],
            "documents": [r[1] or "" for r in rows],
            "metadatas": [r[2] or {} for r in rows],
        }

    # ---- 删除 / 更新 ----

    def delete(
        self, ids: list[str] | None = None, where: dict | None = None,
    ) -> int:
        """删除并返回精确数量（Chroma where 删除无计数，此处为行为增强）。"""
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            if ids is not None:
                cur.execute(
                    f"DELETE FROM {self._table} WHERE collection = %s AND id = ANY(%s) RETURNING id",
                    (self._collection, list(ids)),
                )
                return len(cur.fetchall())
            if where is not None:
                params: list = [self._collection]
                where_sql = where_to_sql(normalize_where(where), params)
                if not where_sql:
                    return 0
                cur.execute(
                    f"DELETE FROM {self._table} WHERE collection = %s AND ({where_sql}) RETURNING id",
                    params,
                )
                return len(cur.fetchall())
        return 0

    def update_metadata_where(self, where: dict, metadata_update: dict) -> int:
        cleaned = _sanitize_metadata(metadata_update)
        params: list = [json.dumps(cleaned, ensure_ascii=False), self._collection]
        where_sql = where_to_sql(normalize_where(where), params)
        if not where_sql:
            return 0
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {self._table} SET metadata = metadata || %s::jsonb "
                f"WHERE collection = %s AND ({where_sql}) RETURNING id",
                params,
            )
            return len(cur.fetchall())


def _collection_name_from_path(persist_directory: str | Path) -> str:
    """路径 → collection 名（basename）。保持与既有 Chroma 实例目录一一对应。"""
    name = Path(str(persist_directory)).name
    return name or "default"
