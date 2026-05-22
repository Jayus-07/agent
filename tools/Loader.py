# =====================================================
# 1. 导入所需的各种Loader
# =====================================================

import os
import hashlib
from langchain_community.document_loaders import (
    TextLoader,  # TXT和MD文件
    PyPDFLoader,  # PDF文件（基于pypdf）
    UnstructuredPDFLoader,  # PDF高级解析（支持OCR布局）
    CSVLoader,  # CSV表格
    Docx2txtLoader,  # Word文档
    UnstructuredMarkdownLoader,  # Markdown专用加载器

)
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from config import CHUNK_SIZE, CHUNK_OVERLAP
from utils.logger import logger


# =====================================================
# 2. 文档拆分工具
# =====================================================

def split_documents(docs, file_path, chunk_size=500, chunk_overlap=50):
    """
    智能拆分文档，保留父文档信息
    
    Args:
        docs: 原始文档列表
        file_path: 文件路径
        chunk_size: Chunk大小
        chunk_overlap: 重叠窗口
    
    Returns:
        拆分后的文档列表，每个文档包含 parent_doc_id
    """
    ext = os.path.splitext(file_path)[1].lower()
    all_chunks = []
    
    # 生成父文档ID
    parent_doc_id = hashlib.md5(file_path.encode()).hexdigest()[:10]
    
    for doc in docs:
        # 根据文件类型选择拆分策略
        if ext == '.md':
            # Markdown：按标题层级拆分
            headers_to_split_on = [
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3"),
            ]
            splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
            sub_docs = splitter.split_text(doc.page_content)
        else:
            # 其他格式：递归字符拆分
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                length_function=len,
                separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
            )
            sub_docs = splitter.split_text(doc.page_content)
        
        # 为每个chunk添加元数据
        for i, chunk in enumerate(sub_docs):
            if isinstance(chunk, str):
                from langchain_core.documents import Document
                chunk_doc = Document(page_content=chunk, metadata=doc.metadata.copy())
            else:
                chunk_doc = chunk
                chunk_doc.metadata = doc.metadata.copy()
            
            # 关键：保留父子关系
            chunk_doc.metadata.update({
                "parent_doc_id": parent_doc_id,
                "chunk_index": i,
                "total_chunks": len(sub_docs),
                "source_file": os.path.basename(file_path),
                "file_path": file_path
            })
            all_chunks.append(chunk_doc)
    
    logger.debug(f"📄 {os.path.basename(file_path)} 拆分为 {len(all_chunks)} 个chunks")
    return all_chunks


# =====================================================
# 3. 多格式加载函数（替代原有loader）
# =====================================================



def load_documents_from_directory(directory_path: str, chunk_size=CHUNK_SIZE,chunk_overlap=CHUNK_OVERLAP):
    """
    批量加载文件夹中的所有文档，自动识别格式并智能拆分
    支持 .txt .md .pdf
    
    Args:
        directory_path: 文档目录路径
        chunk_size: Chunk大小（默认500）
        chunk_overlap: 重叠窗口（默认50）
    """
    all_documents = []

    # 支持的格式与对应的加载器
    file_handlers = {
        ".txt": TextLoader,
        ".md": TextLoader,  # 改用 TextLoader 更稳定，避免 Unstructured 的 zip 错误
        ".pdf": PyPDFLoader,  # 推荐首选PyPDFLoader
        # ".pdf": UnstructuredPDFLoader,  # 需要解析布局时可选用
        # ".pdf": PDFMinerLoader,         # 中文精度高但速度较慢
    }

    # 遍历目录
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            file_path = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()

            # 只处理支持的格式
            if ext in file_handlers:
                try:
                    loader_class = file_handlers[ext]

                    # 特殊处理：MD文件可指定编码
                    if ext in [".md", ".txt"]:
                        loader = loader_class(file_path, encoding="utf-8")
                    else:
                        loader = loader_class(file_path)

                    docs = loader.load()

                    # 智能拆分文档
                    chunks = split_documents(docs, file_path, chunk_size, chunk_overlap)
                    all_documents.extend(chunks)
                    
                    logger.debug(f"✅ 加载成功: {file} ({len(docs)} 页 -> {len(chunks)} chunks)")

                except Exception as e:
                    logger.error(f"❌ 加载失败: {file} - 错误: {e}")

    logger.info(f"📦 总计加载文档块: {len(all_documents)}")
    return all_documents


# =====================================================
# 3. 替换原有的loader
# =====================================================

# 原有代码：
# loader = DirectoryLoader(
#     "data/docs",
#     glob="*.txt",
#     loader_cls=TextLoader,
#     loader_kwargs={"encoding": "utf-8"}
# )
# documents = loader.load()

# 替换为：


