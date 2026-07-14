import os
from langchain_community.document_loaders import (
    TextLoader,
    PyPDFLoader,
)

from config import CHUNK_SIZE, CHUNK_OVERLAP, DEFAULT_KB_ID
from preprocessing.cleaner import DocumentCleaner
from utils.logger import logger


def split_documents(docs, file_path, chunk_size=None, chunk_overlap=None):
    """文档类型感知的智能分块 → 委托给 ChunkStrategyRouter"""
    from preprocessing.chunking import ChunkStrategyRouter

    router = ChunkStrategyRouter(
        fallback_chunk_size=chunk_size or CHUNK_SIZE,
        fallback_chunk_overlap=chunk_overlap or CHUNK_OVERLAP,
    )
    return router.route(docs, file_path)


def load_documents_from_directory(directory_path: str, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    """批量加载文件夹中的所有文档，自动识别格式并智能拆分"""
    all_documents = []

    file_handlers = {
        ".txt": TextLoader,
        ".md": TextLoader,
        ".pdf": PyPDFLoader,
        ".docx": None,  # 在下方特殊处理（需要 Docx2txtLoader）
    }

    for root, dirs, files in os.walk(directory_path):
        # 第一级子目录名 = kb_id
        rel = os.path.relpath(root, directory_path)
        kb_id = rel.split(os.sep)[0] if rel != "." else DEFAULT_KB_ID

        for file in files:
            file_path = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()

            if ext in file_handlers:
                try:
                    loader_class = file_handlers[ext]

                    if ext in [".md", ".txt"]:
                        loader = loader_class(file_path, encoding="utf-8")
                    elif ext == ".docx":
                        from langchain_community.document_loaders import Docx2txtLoader
                        loader = Docx2txtLoader(file_path)
                    else:
                        loader = loader_class(file_path)

                    docs = loader.load()

                    # ── 文档清洗（P0-1）：在分块前清洗文本 ──
                    cleaner = DocumentCleaner()
                    source_type = "pdf" if ext == ".pdf" else "text"
                    for doc in docs:
                        result = cleaner.clean(doc.page_content, source_type=source_type)
                        doc.page_content = result.text

                    chunks = split_documents(docs, file_path, chunk_size, chunk_overlap)

                    # KB 隔离：注入 kb_id 到每个 chunk metadata
                    for c in chunks:
                        c.metadata["kb_id"] = kb_id

                    all_documents.extend(chunks)

                    logger.debug(f"✅ 加载成功: {file} (kb={kb_id}, {len(docs)} 页 -> {len(chunks)} chunks)")

                except Exception as e:
                    logger.error(f"❌ 加载失败: {file} - 错误: {e}")

    logger.info(f"\U0001f4e6 总计加载文档块: {len(all_documents)}")
    return all_documents
