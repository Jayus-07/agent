"""PGFullTextRetriever — 基于 PostgreSQL GIN 倒排索引的毫秒级增量全文检索器。

替代旧有单机 Python rank_bm25 + pickle 序列化方案：
1. 真正实现行级原子增量写入（无需读取全量 1.8 万文档重算 IDF 矩阵）。
2. 利用 PostgreSQL 内置 GIN 倒排索引与 ts_rank_cd 覆盖密度打分（数学行为高度对齐 BM25）。
3. 零额外容器、零新增内存开销，彻底破除 4C8G 容器环境 OOM 风险。
4. 产出 Document 携带 metadata["bm25_score"]，100% 兼容上层 RRF 融合与重排链路。
"""

from __future__ import annotations

import re
import jieba
from typing import Any, List, Optional
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
import psycopg2
import psycopg2.extras

from backend.config.database import VECTOR_PG_CONFIG
from backend.config.rag import BM25_CANDIDATE_K
from backend.shared.logger import logger

_CLEAN_TERM_RE = re.compile(r"[\s\',:\(\)\|\&!<>\*]+", re.UNICODE)


def _tokenize_for_pg_tsquery(text: str) -> list[str]:
    """将中文和英文查询分词，过滤无意义符号，返回清洗后的 token 列表。"""
    if not text or not text.strip():
        return []

    raw_tokens = list(jieba.cut_for_search(text.strip()))
    cleaned_tokens: list[str] = []
    seen = set()

    for tok in raw_tokens:
        tok_clean = _CLEAN_TERM_RE.sub("", tok).strip()
        if not tok_clean or len(tok_clean) < 1:
            continue
        if tok_clean in seen:
            continue
        seen.add(tok_clean)
        cleaned_tokens.append(tok_clean)

    return cleaned_tokens


def build_simple_tsquery(text: str) -> str:
    """构建 PostgreSQL to_tsquery('simple', ...) 兼容的 OR 查询表达式。"""
    tokens = _tokenize_for_pg_tsquery(text)
    if not tokens:
        return ""
    # 用单引号包裹每个 term，以 | (OR) 连接
    return " | ".join(f"'{tok}'" for tok in tokens)


class PGFullTextRetriever(BaseRetriever):
    """基于 PostgreSQL GIN 倒排索引的全文检索器，替代单机 BM25Retriever。"""

    collection: str = "chroma"
    k: int = BM25_CANDIDATE_K
    kb_id: Optional[str] = None
    table_name: str = "rag_vectors"

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: Optional[CallbackManagerForRetrieverRun] = None
    ) -> List[Document]:
        """执行全文倒排检索，返回排序后的 Document 列表。"""
        tsquery_str = build_simple_tsquery(query)
        if not tsquery_str:
            return []

        docs: List[Document] = []
        try:
            with psycopg2.connect(**VECTOR_PG_CONFIG) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    # 基础 SQL
                    sql = f"""
                        SELECT id, doc_id, content, metadata,
                               ts_rank_cd(tsv_content, to_tsquery('simple', %s), 32) AS rank_score
                        FROM {self.table_name}
                        WHERE collection = %s
                          AND tsv_content @@ to_tsquery('simple', %s)
                    """
                    params: list[Any] = [tsquery_str, self.collection, tsquery_str]

                    # 若指定 kb_id 过滤
                    if self.kb_id:
                        sql += " AND (metadata->>'kb_id' = %s) "
                        params.append(self.kb_id)

                    sql += " ORDER BY rank_score DESC LIMIT %s;"
                    params.append(self.k)

                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()

                    for r in rows:
                        meta = dict(r["metadata"] or {})
                        score = float(r["rank_score"] or 0.0)
                        meta["doc_id"] = r["doc_id"] or meta.get("doc_id", "")
                        meta["bm25_score"] = score
                        meta["vector_id"] = r["id"]

                        doc = Document(
                            page_content=r["content"] or "",
                            metadata=meta,
                        )
                        docs.append(doc)
        except Exception as e:
            logger.warning(f"[PGFullTextRetriever] 全文倒排检索异常 (query={query[:50]}): {e}")
            return []

        return docs
