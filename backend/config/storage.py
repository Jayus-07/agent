"""config/storage.py — 存储路径配置

Chromadb / BM25 / 文档目录 / 上传目录等运行时数据路径。

注：这些路径在 database.py 中也定义了（CHROMA_PATH 等），
   这里提供带前缀的别名，便于在 storage 相关代码中统一引用。
"""
from backend.config.database import (
    CHROMA_PATH,
    BM25_INDEX_DIR,
    DOCS_DIRECTORY,
    DOC_REGISTRY_PATH,
)

STORAGE_CHROMA_PATH = CHROMA_PATH
STORAGE_BM25_DIR = BM25_INDEX_DIR
STORAGE_DOCS_DIR = DOCS_DIRECTORY
STORAGE_DOC_REGISTRY = DOC_REGISTRY_PATH