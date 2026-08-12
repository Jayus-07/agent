"""loader.py — 文档加载入口（委托新流水线 parse_and_chunk 分块）。"""
import hashlib
import os

from backend.config import DEFAULT_KB_ID
from backend.shared.logger import logger


def load_documents_from_directory(directory_path: str, chunk_size=None, chunk_overlap=None):
    """批量加载文件夹中的所有文档，用新流水线 parse_and_chunk 智能分块。

    chunk_size / chunk_overlap 参数保留仅为向后兼容，实际分块参数由
    parse_and_chunk → 各 Strategy 从 config 读取。

    注入 kb_id（首级子目录）与 doc_id（与 indexer._derive_doc_id 同源），
    保证 BM25 与增量索引使用同一 doc_id，级联删除/检索过滤能按 doc_id 命中。
    注意：department 与 indexer 默认一致（"general"），多部门场景需抽公共派生函数。
    """
    from backend.rag.preprocessing.pipeline import parse_and_chunk

    all_documents = []

    for root, _dirs, files in os.walk(directory_path):
        # 第一级子目录名 = kb_id
        rel_root = os.path.relpath(root, directory_path)
        kb_id = rel_root.split(os.sep)[0] if rel_root != "." else DEFAULT_KB_ID

        for file in files:
            file_path = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()
            if ext not in (".md", ".txt"):
                continue  # Phase 1 仅支持 md/txt；pdf/docx 延后 Phase 2

            try:
                chunks = parse_and_chunk(file_path)
                rel_path = os.path.relpath(file_path, directory_path).replace("\\", "/")
                # 与 indexer._derive_doc_id 同源：sha256(kb_id|department|rel_path)[:16]
                doc_id = hashlib.sha256(
                    f"{kb_id}|general|{rel_path}".encode("utf-8")
                ).hexdigest()[:16]
                for c in chunks:
                    c.metadata["kb_id"] = kb_id
                    c.metadata["doc_id"] = doc_id
                all_documents.extend(chunks)
                logger.debug(f"[loader] 加载成功: {file} (kb={kb_id}, {len(chunks)} chunks)")
            except Exception as e:
                logger.error(f"[loader] 加载失败: {file} - 错误: {e}")

    logger.info(f"[loader] 总计加载文档块: {len(all_documents)}")
    return all_documents
