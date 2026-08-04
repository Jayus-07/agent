"""config/database.py — 数据路径 + PostgreSQL 连接

包含 RAG 索引路径、PostgreSQL 连接、SQLAlchemy 等数据源配置。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 基础路径（基于本文件位置，消除 CWD 依赖）
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_CONFIG_DIR)  # agent/backend/

# RAG 索引路径
BM25_INDEX_DIR = os.getenv("BM25_INDEX_DIR", os.path.join(_BACKEND_DIR, "data", "bm25"))
CHROMA_PATH = os.getenv("CHROMA_PATH", os.path.join(_BACKEND_DIR, "data", "chroma"))
DOC_DB_PATH = os.getenv("DOC_DB_PATH", os.path.join(_BACKEND_DIR, "data", "doc_db"))

DOCS_DIRECTORY = os.getenv("DOCS_DIRECTORY", os.path.join(_BACKEND_DIR, "data", "docs"))
DOC_REGISTRY_PATH = os.getenv("DOC_REGISTRY_PATH", os.path.join(_BACKEND_DIR, "data", "doc_registry.db"))
DOC_OPERATION_LOG_PATH = os.getenv("DOC_OPERATION_LOG_PATH", os.path.join(_BACKEND_DIR, "data", "doc_operation_log.db"))
ENABLE_INCREMENTAL_INDEXING = os.getenv("ENABLE_INCREMENTAL_INDEXING", "true").lower() == "true"

# Memory — Enterprise PostgreSQL
DB_CONFIG = {
    "host":     os.getenv("PGHOST", "localhost"),
    "port":     int(os.getenv("PGPORT", "5432")),
    "dbname":   os.getenv("PGDATABASE", "demo"),
    "user":     os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "123456"),
}