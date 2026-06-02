import os
import hashlib
from langchain_community.document_loaders import (
    TextLoader,
    PyPDFLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from config import CHUNK_SIZE, CHUNK_OVERLAP
from utils.logger import logger


def split_documents(docs, file_path, chunk_size=500, chunk_overlap=50):
    """智能拆分文档，保留父文档信息"""
    ext = os.path.splitext(file_path)[1].lower()
    all_chunks = []

    parent_doc_id = hashlib.md5(file_path.encode()).hexdigest()[:10]

    for doc in docs:
        if ext == '.md':
            headers_to_split_on = [
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3"),
            ]
            splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
            sub_docs = splitter.split_text(doc.page_content)
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                length_function=len,
                separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
            )
            sub_docs = splitter.split_text(doc.page_content)

        for i, chunk in enumerate(sub_docs):
            if isinstance(chunk, str):
                from langchain_core.documents import Document
                chunk_doc = Document(page_content=chunk, metadata=doc.metadata.copy())
            else:
                chunk_doc = chunk
                chunk_doc.metadata = doc.metadata.copy()

            chunk_doc.metadata.update({
                "parent_doc_id": parent_doc_id,
                "chunk_index": i,
                "total_chunks": len(sub_docs),
                "source_file": os.path.basename(file_path),
                "file_path": file_path
            })
            all_chunks.append(chunk_doc)

    logger.debug(f"\U0001f4c4 {os.path.basename(file_path)} 拆分为 {len(all_chunks)} 个chunks")
    return all_chunks


def load_documents_from_directory(directory_path: str, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    """批量加载文件夹中的所有文档，自动识别格式并智能拆分"""
    all_documents = []

    file_handlers = {
        ".txt": TextLoader,
        ".md": TextLoader,
        ".pdf": PyPDFLoader,
    }

    for root, dirs, files in os.walk(directory_path):
        for file in files:
            file_path = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()

            if ext in file_handlers:
                try:
                    loader_class = file_handlers[ext]

                    if ext in [".md", ".txt"]:
                        loader = loader_class(file_path, encoding="utf-8")
                    else:
                        loader = loader_class(file_path)

                    docs = loader.load()
                    chunks = split_documents(docs, file_path, chunk_size, chunk_overlap)
                    all_documents.extend(chunks)

                    logger.debug(f"✅ 加载成功: {file} ({len(docs)} 页 -> {len(chunks)} chunks)")

                except Exception as e:
                    logger.error(f"❌ 加载失败: {file} - 错误: {e}")

    logger.info(f"\U0001f4e6 总计加载文档块: {len(all_documents)}")
    return all_documents
